"""Core clustering engine for grouping HTTP transactions into API endpoints."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from urllib.parse import urlparse

from har2mcp.cluster.graphql import extract_graphql_operation, is_graphql_entry
from har2mcp.cluster.models import EndpointCluster, ObservedQueryParams
from har2mcp.cluster.normalizer import (
    are_paths_compatible,
    normalize_path_cluster,
    split_path,
)
from har2mcp.parser.models import Entry

DYNAMIC_QUERY_NAMES: frozenset[str] = frozenset(
    {
        "q",
        "query",
        "search",
        "page",
        "limit",
        "offset",
        "skip",
        "cursor",
        "filter",
        "sort",
        "order",
        "id",
        "ids",
        "since",
        "until",
        "token",
        "api_key",
    }
)


def analyze_query_params(entries: Sequence[Entry]) -> ObservedQueryParams:
    """Analyze query parameters across requests to partition into stable vs variable.

    Args:
        entries: Grouped requests for a specific endpoint.

    Returns:
        ObservedQueryParams classifying stable, variable, and optional parameters.
    """
    total_calls = len(entries)
    if total_calls == 0:
        return ObservedQueryParams()

    param_values: dict[str, list[str]] = defaultdict(list)
    call_occurrences: dict[str, int] = defaultdict(int)

    for entry in entries:
        seen_in_this_call: set[str] = set()
        for q in entry.request.query_string:
            param_values[q.name].append(q.value)
            seen_in_this_call.add(q.name)

        for name in seen_in_this_call:
            call_occurrences[name] += 1

    stable: dict[str, str] = {}
    variable: list[str] = []
    optional: list[str] = []

    for name, values in param_values.items():
        distinct_vals = set(values)
        is_optional = call_occurrences[name] < total_calls
        if is_optional:
            optional.append(name)

        if len(distinct_vals) == 1 and name.lower() not in DYNAMIC_QUERY_NAMES:
            stable[name] = values[0]
        else:
            variable.append(name)

    return ObservedQueryParams(
        stable=stable,
        variable=sorted(variable),
        optional=sorted(optional),
    )


def cluster_entries(entries: Sequence[Entry]) -> list[EndpointCluster]:
    """Group entries by method and normalized path, splitting GraphQL operations.

    Args:
        entries: Candidate API exchanges from parser/redact.

    Returns:
        Consolidated list of EndpointClusters with query param analysis.
    """
    if not entries:
        return []

    graphql_buckets: dict[str, list[Entry]] = defaultdict(list)
    # REST bucketed by (method, segment_count)
    rest_buckets: dict[tuple[str, int], list[Entry]] = defaultdict(list)

    for entry in entries:
        if is_graphql_entry(entry):
            op_name = extract_graphql_operation(entry) or "AnonymousOperation"
            graphql_buckets[op_name].append(entry)
        else:
            method = entry.request.method.upper()
            path = urlparse(entry.request.url).path
            segs = split_path(path)
            rest_buckets[(method, len(segs))].append(entry)

    clusters: list[EndpointCluster] = []

    # 1. Process GraphQL pseudo-endpoints
    for op_name, op_entries in graphql_buckets.items():
        method = op_entries[0].request.method.upper()
        path_template = f"/graphql#{op_name}"
        query_analysis = analyze_query_params(op_entries)
        hosts = {
            urlparse(e.request.url).hostname or ""
            for e in op_entries
            if urlparse(e.request.url).hostname
        }

        cluster = EndpointCluster(
            method=method,
            path_template=path_template,
            sample_requests=[e.request for e in op_entries[:5]],
            sample_responses=[e.response for e in op_entries[:5]],
            observed_query_params=query_analysis,
            call_count=len(op_entries),
            path_params=[],
            is_graphql=True,
            operation_name=op_name,
            hosts=hosts,
            entries=list(op_entries),
        )
        clusters.append(cluster)

    # 2. Process REST endpoints by equivalence classes
    for (method, _), bucket_entries in rest_buckets.items():
        # Sub-group into compatible routes
        route_groups: list[list[Entry]] = []

        for entry in bucket_entries:
            path = urlparse(entry.request.url).path
            segs = split_path(path)
            matched_group = False

            for group in route_groups:
                rep_path = urlparse(group[0].request.url).path
                rep_segs = split_path(rep_path)
                if are_paths_compatible(segs, rep_segs):
                    group.append(entry)
                    matched_group = True
                    break

            if not matched_group:
                route_groups.append([entry])

        for group in route_groups:
            paths = [urlparse(e.request.url).path for e in group]
            path_template, path_params = normalize_path_cluster(paths)
            query_analysis = analyze_query_params(group)
            hosts = {
                urlparse(e.request.url).hostname or ""
                for e in group
                if urlparse(e.request.url).hostname
            }

            cluster = EndpointCluster(
                method=method,
                path_template=path_template,
                sample_requests=[e.request for e in group[:5]],
                sample_responses=[e.response for e in group[:5]],
                observed_query_params=query_analysis,
                call_count=len(group),
                path_params=path_params,
                is_graphql=False,
                operation_name=None,
                hosts=hosts,
                entries=list(group),
            )
            clusters.append(cluster)

    # Sort clusters deterministically
    clusters.sort(key=lambda c: (c.path_template, c.method))
    return clusters


# Convenient alias
cluster = cluster_entries
