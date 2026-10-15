# -------------------------------------------------------------------------
# SudoSync for Kodi
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# -------------------------------------------------------------------------
"""Small, mockable Kodi JSON-RPC adapter for SudoSync v2.

This module intentionally has no import-time dependency on xbmc. Production
code passes xbmc.executeJSONRPC as the transport; unit tests use a fake.
"""
from __future__ import absolute_import

import json
import logging
import math
import posixpath
import xml.etree.ElementTree as ET

from .identity import normalize_identifier

logger = logging.getLogger(__name__)


def is_aborted(abort_requested):
    if abort_requested is None:
        return False
    if callable(abort_requested):
        return bool(abort_requested())
    checker = getattr(abort_requested, "abortRequested", None)
    if callable(checker):
        return bool(checker())
    return False


class OperationAborted(RuntimeError):
    """Operation was aborted by abortRequested signal."""

    def __init__(self, message="Operation aborted", partial_items=None):
        super(OperationAborted, self).__init__(message)
        self.partial_items = partial_items if partial_items is not None else []



MOVIE_PROPERTIES = (
    "title", "year", "imdbnumber", "uniqueid", "playcount", "lastplayed",
    "resume", "rating", "userrating", "file",
)
EPISODE_PROPERTIES = (
    "title", "season", "episode", "showtitle", "uniqueid", "imdbnumber",
    "playcount", "lastplayed", "resume", "rating", "userrating", "file",
    "tvshowid",
)
_SYNC_FIELDS = frozenset(("playcount", "lastplayed", "resume", "userrating"))


class KodiRpcError(RuntimeError):
    """Kodi JSON-RPC transport, protocol, or method error."""


class KodiJsonRpcAdapter(object):
    """Kodi JSON-RPC wrapper with bounded pagination and strict response checks."""

    def __init__(self, execute_jsonrpc, page_size=500, max_records=100000,
                 nfo_reader=None, max_nfo_bytes=256 * 1024,
                 abort_requested=None):
        if not callable(execute_jsonrpc):
            raise ValueError("execute_jsonrpc must be callable")
        if type(page_size) is not int or not 1 <= page_size <= 5000:
            raise ValueError("page_size must be between 1 and 5000")
        if type(max_records) is not int or not 1 <= max_records <= 1000000:
            raise ValueError("max_records must be between 1 and 1000000")
        if nfo_reader is not None and not callable(nfo_reader):
            raise ValueError("nfo_reader must be callable when provided")
        if type(max_nfo_bytes) is not int or not 1 <= max_nfo_bytes <= 4 * 1024 * 1024:
            raise ValueError("max_nfo_bytes must be between 1 byte and 4 MiB")
        self._execute_jsonrpc = execute_jsonrpc
        self._page_size = page_size
        self._max_records = max_records
        self._nfo_reader = nfo_reader
        self._max_nfo_bytes = max_nfo_bytes
        self._abort_requested = abort_requested
        self._skipped_nfos = []
        self._request_id = 0

    @property
    def skipped_nfos(self):
        return list(self._skipped_nfos)

    def clear_skipped_nfos(self):
        self._skipped_nfos = []

    def _is_aborted(self, abort_requested=None):
        target = abort_requested if abort_requested is not None else self._abort_requested
        return is_aborted(target)

    def _record_nfo_error(self, path, reason):
        entry = {"path": path, "reason": str(reason)}
        self._skipped_nfos.append(entry)
        logger.warning("Skipping corrupted metadata file %s: %s", path, reason)

    def _nfo_paths(self, media_path, media_type):
        if not isinstance(media_path, str) or not media_path.strip():
            return ()
        folder, filename = posixpath.split(media_path.replace("\\", "/"))
        stem = filename.rsplit(".", 1)[0] if "." in filename else filename
        candidates = [posixpath.join(folder, stem + ".nfo")]
        if media_type == "movie":
            movie_nfo = posixpath.join(folder, "movie.nfo")
            if movie_nfo not in candidates:
                candidates.append(movie_nfo)
        return tuple(candidates)

    def _read_single_nfo(self, path):
        raw = self._nfo_reader(path, self._max_nfo_bytes)
        if raw is None:
            return None
        if raw == "" or raw == b"":
            raise ValueError("Metadata file is empty: " + path)
        if isinstance(raw, bytes):
            if len(raw) > self._max_nfo_bytes:
                raise ValueError("Metadata file exceeds configured size limit: " + path)
            raw = raw.decode("utf-8-sig")
        elif isinstance(raw, str):
            if len(raw.encode("utf-8")) > self._max_nfo_bytes:
                raise ValueError("Metadata file exceeds configured size limit: " + path)
        else:
            raise ValueError("Metadata reader returned an invalid value for " + path)
        root = ET.fromstring(raw)
        identifiers = {}
        for node in root.findall("uniqueid"):
            kind = (node.attrib.get("type") or "").strip().lower()
            value = normalize_identifier(kind, node.text)
            if value is not None:
                if kind in identifiers and identifiers[kind] != value:
                    raise ValueError("Metadata file has conflicting {} identifiers: {}".format(kind, path))
                identifiers[kind] = value
        return root, identifiers

    def _read_nfo_ids(self, media_path, media_type, abort_requested=None):
        if self._nfo_reader is None:
            return {}
        for path in self._nfo_paths(media_path, media_type):
            if self._is_aborted(abort_requested):
                raise OperationAborted("NFO reading aborted before " + path)
            try:
                res = self._read_single_nfo(path)
                if res is None:
                    continue
                root, identifiers = res
                if media_type == "movie":
                    tmdb = normalize_identifier("tmdb", root.findtext("tmdbid"))
                    legacy_imdb = normalize_identifier("imdb", root.findtext("id"))
                    for kind, value in (("tmdb", tmdb), ("imdb", legacy_imdb)):
                        if value is not None:
                            if kind in identifiers and identifiers[kind] != value:
                                raise ValueError("Metadata file has conflicting {} identifiers: {}".format(kind, path))
                            identifiers[kind] = value
                return identifiers
            except (IOError, OSError) as exc:
                reason = "Cannot read metadata file: {}".format(exc)
                self._record_nfo_error(path, reason)
                continue
            except UnicodeDecodeError as exc:
                reason = "Metadata file is not valid UTF-8: {}".format(exc)
                self._record_nfo_error(path, reason)
                continue
            except (ET.ParseError, ValueError) as exc:
                reason = "Metadata file parsing error: {}".format(exc)
                self._record_nfo_error(path, reason)
                continue
        return {}

    def _enrich_with_nfo(self, items, media_type, abort_requested=None):
        if self._nfo_reader is None:
            return items
        enriched = []
        for item in items:
            if self._is_aborted(abort_requested):
                raise OperationAborted("Item enrichment aborted", partial_items=enriched)
            if not isinstance(item, dict):
                raise KodiRpcError("Kodi returned an invalid {} library record".format(media_type))
            record = dict(item)
            kodi_ids = record.get("uniqueid")
            kodi_ids = dict(kodi_ids) if isinstance(kodi_ids, dict) else {}
            if record.get("imdbnumber"):
                imdb = normalize_identifier("imdb", record.get("imdbnumber"))
                if imdb is not None:
                    if "imdb" in kodi_ids and normalize_identifier("imdb", kodi_ids["imdb"]) != imdb:
                        raise KodiRpcError("Kodi returned conflicting IMDb identifiers for one library item")
                    kodi_ids["imdb"] = imdb
            try:
                nfo_ids = self._read_nfo_ids(record.get("file"), media_type, abort_requested=abort_requested)
            except OperationAborted as exc:
                exc.partial_items = enriched
                raise
            for kind, value in nfo_ids.items():
                current = normalize_identifier(kind, kodi_ids.get(kind))
                if current is not None and current != value:
                    raise KodiRpcError("Kodi and NFO contain conflicting {} identifiers for one library item".format(kind))
                kodi_ids[kind] = value
            record["uniqueid"] = kodi_ids
            enriched.append(record)
        return enriched

    def call(self, method, params=None, abort_requested=None):
        if self._is_aborted(abort_requested):
            raise OperationAborted("JSON-RPC aborted before call: " + method)
        if not isinstance(method, str) or not method.strip() or any(ch.isspace() for ch in method):
            raise ValueError("method must be a non-empty JSON-RPC method name")
        if params is not None and not isinstance(params, dict):
            raise ValueError("params must be an object")
        self._request_id += 1
        request_id = self._request_id
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        try:
            raw = self._execute_jsonrpc(json.dumps(payload, ensure_ascii=False))
        except Exception as exc:
            raise KodiRpcError("Kodi JSON-RPC transport failed for {}: {}".format(method, exc))
        if isinstance(raw, bytes):
            try:
                raw = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise KodiRpcError("Kodi returned non-UTF-8 JSON-RPC data for {}: {}".format(method, exc))
        if not isinstance(raw, str):
            raise KodiRpcError("Kodi returned a non-text JSON-RPC response for {}".format(method))
        try:
            response = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise KodiRpcError("Kodi returned invalid JSON-RPC data for {}: {}".format(method, exc))
        if not isinstance(response, dict) or response.get("id") != request_id:
            raise KodiRpcError("Kodi returned a mismatched JSON-RPC response for {}".format(method))
        if "error" in response:
            error = response.get("error")
            if isinstance(error, dict):
                code, message = error.get("code"), error.get("message", "unknown error")
            else:
                code, message = None, "malformed JSON-RPC error"
            raise KodiRpcError("Kodi JSON-RPC {} failed ({}): {}".format(method, code, message))
        if "result" not in response:
            raise KodiRpcError("Kodi JSON-RPC response for {} has no result".format(method))
        return response["result"]

    def get_version(self):
        result = self.call("Application.GetProperties", {"properties": ["version", "name"]})
        if not isinstance(result, dict) or not isinstance(result.get("version"), dict):
            raise KodiRpcError("Application.GetProperties returned an invalid version")
        version = result["version"]
        parts = []
        for key in ("major", "minor", "patch"):
            value = version.get(key)
            if type(value) is not int or value < 0:
                raise KodiRpcError("Kodi returned an invalid version component: " + key)
            parts.append(value)
        return {"name": result.get("name", "Kodi"), "major": parts[0], "minor": parts[1], "patch": parts[2]}

    def _get_library_items(self, method, collection_key, properties, abort_requested=None):
        all_items = []
        start = 0
        expected_total = None
        while True:
            if self._is_aborted(abort_requested):
                raise OperationAborted("{} aborted during pagination".format(method), partial_items=all_items)
            end = start + self._page_size
            result = self.call(method, {
                "properties": list(properties),
                "limits": {"start": start, "end": end},
                "sort": {"method": "title", "order": "ascending"},
            }, abort_requested=abort_requested)
            if not isinstance(result, dict) or not isinstance(result.get(collection_key), list):
                raise KodiRpcError("{} returned an invalid {} collection".format(method, collection_key))
            items = result[collection_key]
            limits = result.get("limits")
            total = limits.get("total") if isinstance(limits, dict) else None
            if total is not None:
                if type(total) is not int or total < 0:
                    raise KodiRpcError("{} returned an invalid total count".format(method))
                if total > self._max_records:
                    raise KodiRpcError("{} exceeds the configured library record limit".format(method))
                if expected_total is None:
                    expected_total = total
                elif total != expected_total:
                    raise KodiRpcError("{} total changed during pagination; refusing an inconsistent snapshot".format(method))
            if len(all_items) + len(items) > self._max_records:
                raise KodiRpcError("{} exceeds the configured library record limit".format(method))
            for item in items:
                if self._is_aborted(abort_requested):
                    raise OperationAborted("{} aborted while processing items".format(method), partial_items=all_items)
                all_items.append(item)
            if expected_total is not None and len(all_items) > expected_total:
                raise KodiRpcError("{} returned more records than its reported total".format(method))
            if expected_total is not None and len(all_items) == expected_total:
                break
            if not items:
                if expected_total is None or len(all_items) >= expected_total:
                    break
                raise KodiRpcError("{} pagination stopped before all records were read".format(method))
            if expected_total is None and len(items) < self._page_size:
                break
            start += len(items)
        return all_items

    def get_movies(self, abort_requested=None):
        items = self._get_library_items("VideoLibrary.GetMovies", "movies", MOVIE_PROPERTIES, abort_requested=abort_requested)
        return self._enrich_with_nfo(items, "movie", abort_requested=abort_requested)

    def get_episodes(self, abort_requested=None):
        items = self._get_library_items("VideoLibrary.GetEpisodes", "episodes", EPISODE_PROPERTIES, abort_requested=abort_requested)
        return self._enrich_with_nfo(items, "episode", abort_requested=abort_requested)

    def get_movie_details(self, movie_id):
        if type(movie_id) is not int or movie_id < 1:
            raise ValueError("movie_id must be a positive local Kodi ID")
        result = self.call("VideoLibrary.GetMovieDetails", {
            "movieid": movie_id,
            "properties": list(MOVIE_PROPERTIES),
        })
        details = result.get("moviedetails") if isinstance(result, dict) else None
        if not isinstance(details, dict):
            raise KodiRpcError("VideoLibrary.GetMovieDetails returned invalid details")
        return details

    def get_episode_details(self, episode_id):
        if type(episode_id) is not int or episode_id < 1:
            raise ValueError("episode_id must be a positive local Kodi ID")
        result = self.call("VideoLibrary.GetEpisodeDetails", {
            "episodeid": episode_id,
            "properties": list(EPISODE_PROPERTIES),
        })
        details = result.get("episodedetails") if isinstance(result, dict) else None
        if not isinstance(details, dict):
            raise KodiRpcError("VideoLibrary.GetEpisodeDetails returned invalid details")
        return details

    def is_video_playing(self, abort_requested=None):
        if self._is_aborted(abort_requested):
            raise OperationAborted("Video playback check aborted")
        players = self.call("Player.GetActivePlayers", abort_requested=abort_requested)
        if not isinstance(players, list):
            raise KodiRpcError("Player.GetActivePlayers returned an invalid response")
        return any(isinstance(player, dict) and player.get("type") == "video" for player in players)

    @staticmethod
    def _validate_sync_details(details):
        if not isinstance(details, dict) or not details:
            raise ValueError("details must be a non-empty object")
        unknown = set(details) - _SYNC_FIELDS
        if unknown:
            raise ValueError("unsupported synchronized fields: " + ", ".join(sorted(unknown)))
        result = dict(details)
        if "playcount" in result and (type(result["playcount"]) is not int or result["playcount"] < 0):
            raise ValueError("playcount must be a non-negative integer")
        if "lastplayed" in result and not isinstance(result["lastplayed"], str):
            raise ValueError("lastplayed must be a string")
        if "userrating" in result:
            value = result["userrating"]
            if value is not None and (type(value) is not int or not 1 <= value <= 10):
                raise ValueError("userrating must be null or an integer from 1 to 10")
        if "resume" in result:
            resume = result["resume"]
            if not isinstance(resume, dict) or set(resume) != {"position", "total"}:
                raise ValueError("resume must contain exactly position and total")
            for key in ("position", "total"):
                value = resume[key]
                if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                    raise ValueError("resume {} must be a finite non-negative number".format(key))
            if result["resume"]["position"] > result["resume"]["total"] + 2.0 and result["resume"]["total"] != 0:
                raise ValueError("resume position exceeds total duration")
        return result

    def set_movie_details(self, movie_id, details):
        if type(movie_id) is not int or movie_id < 1:
            raise ValueError("movie_id must be a positive local Kodi ID")
        validated = self._validate_sync_details(details)
        return self.call("VideoLibrary.SetMovieDetails", dict({"movieid": movie_id}, **validated))

    def set_episode_details(self, episode_id, details):
        if type(episode_id) is not int or episode_id < 1:
            raise ValueError("episode_id must be a positive local Kodi ID")
        validated = self._validate_sync_details(details)
        return self.call("VideoLibrary.SetEpisodeDetails", dict({"episodeid": episode_id}, **validated))
