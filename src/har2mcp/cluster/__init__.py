"""Clustering and URL pattern normalization for API endpoints."""

from har2mcp.cluster.engine import cluster, cluster_entries
from har2mcp.cluster.graphql import extract_graphql_operation, is_graphql_entry
from har2mcp.cluster.models import EndpointCluster, ObservedQueryParams
from har2mcp.cluster.normalizer import (
    are_paths_compatible,
    derive_param_name,
    looks_like_id,
    normalize_path_cluster,
    singularize,
    split_path,
)

__all__ = [
    "EndpointCluster",
    "ObservedQueryParams",
    "are_paths_compatible",
    "cluster",
    "cluster_entries",
    "derive_param_name",
    "extract_graphql_operation",
    "is_graphql_entry",
    "looks_like_id",
    "normalize_path_cluster",
    "singularize",
    "split_path",
]
