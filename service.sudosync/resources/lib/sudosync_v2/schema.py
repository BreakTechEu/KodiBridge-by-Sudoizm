"""Strict, side-effect-free validation for SudoSync v2 shared snapshots."""
from __future__ import absolute_import
import math
import re
import uuid
from . import FORMAT_NAME, SCHEMA_VERSION, SYNC_FIELDS, IDENTIFIER_TYPES
from .identity import normalize_identifier

_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")

class SnapshotValidationError(ValueError):
    """Raised when shared data is malformed or outside supported limits."""

def _require(condition, message):
    if not condition:
        raise SnapshotValidationError(message)

def _valid_timestamp(value):
    if not isinstance(value, str) or not _TIMESTAMP.fullmatch(value):
        return False
    try:
        from datetime import datetime
        datetime.strptime(value.split(".", 1)[0].rstrip("Z"), "%Y-%m-%dT%H:%M:%S")
        return True
    except ValueError:
        return False

def _validate_state(state, where):
    _require(isinstance(state, dict), where + ": state must be an object")
    _require(set(state) == set(SYNC_FIELDS), where + ": state must contain exactly the supported fields")
    count = state["playcount"]
    _require(type(count) is int and 0 <= count <= 2147483647, where + ": invalid playcount")
    lastplayed = state["lastplayed"]
    _require(lastplayed == "" or _valid_timestamp(lastplayed), where + ": invalid lastplayed timestamp")
    resume = state["resume"]
    _require(isinstance(resume, dict) and set(resume) == {"position", "total"}, where + ": invalid resume object")
    for key in ("position", "total"):
        value = resume[key]
        _require(type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1000000000,
                 where + ": invalid resume " + key)
    _require(resume["position"] <= resume["total"] + 2.0 or resume["total"] == 0,
             where + ": resume position exceeds media duration")
    rating = state["userrating"]
    _require(rating is None or (type(rating) is int and 1 <= rating <= 10), where + ": invalid userrating")

def validate_snapshot(snapshot, max_records=100000):
    """Validate a complete snapshot and return None; raise on any unsafe shape."""
    _require(isinstance(snapshot, dict), "snapshot must be an object")
    _require(snapshot.get("format") == FORMAT_NAME, "unsupported snapshot format")
    _require(snapshot.get("schema_version") == SCHEMA_VERSION, "unsupported snapshot schema version")
    client = snapshot.get("client")
    _require(isinstance(client, dict) and set(client) == {"id", "name"}, "invalid client object")
    try:
        parsed = uuid.UUID(str(client["id"]))
        _require(str(parsed) == str(client["id"]).lower(), "client id must be a canonical UUID")
    except (ValueError, TypeError, AttributeError):
        raise SnapshotValidationError("client id must be a canonical UUID")
    _require(isinstance(client["name"], str) and 1 <= len(client["name"].strip()) <= 128, "invalid client name")
    _require(_valid_timestamp(snapshot.get("generated_at")), "invalid generated_at timestamp")
    movies, episodes = snapshot.get("movies"), snapshot.get("episodes")
    _require(isinstance(movies, list) and isinstance(episodes, list), "movies and episodes must be arrays")
    _require(type(max_records) is int and max_records >= 0, "invalid record limit")
    _require(len(movies) + len(episodes) <= max_records, "snapshot exceeds record limit")
    for collection, expected_type in ((movies, "movie"), (episodes, "episode")):
        for index, record in enumerate(collection):
            where = "{}[{}]".format(expected_type, index)
            _require(isinstance(record, dict), where + ": record must be an object")
            _require(record.get("type") == expected_type, where + ": media type does not match collection")
            ids = record.get("ids")
            _require(isinstance(ids, dict) and set(ids).issubset(set(IDENTIFIER_TYPES)), where + ": invalid identifiers object")
            for kind, value in ids.items():
                _require(normalize_identifier(kind, value) is not None, where + ": invalid " + kind + " identifier")
            _validate_state(record.get("state"), where)
            versions = record.get("versions")
            _require(isinstance(versions, dict) and set(versions) == set(SYNC_FIELDS), where + ": invalid field versions")
            for field in SYNC_FIELDS:
                version = versions[field]
                _require(isinstance(version, dict) and set(version) == {"lc", "seq", "client_id"},
                         where + ": invalid version for " + field)
                _require(type(version["lc"]) is int and 0 <= version["lc"] <= 2147483647,
                         where + ": invalid logical clock for " + field)
                _require(type(version["seq"]) is int and 0 <= version["seq"] <= 2147483647,
                         where + ": invalid sequence for " + field)
                _require(isinstance(version["client_id"], str) and len(version["client_id"]) <= 128,
                         where + ": invalid version client id")
    return None
