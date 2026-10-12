"""Discover Klei Don't Starve animation XML and converted atlas pages."""

from __future__ import annotations

import hashlib
import math
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


ADAPTER_ID = "klei_dst"


def stable_id(kind: str, *parts: str) -> str:
    value = "\0".join((ADAPTER_ID, kind, *parts)).encode("utf-8")
    return f"{ADAPTER_ID}:{kind}:{hashlib.sha256(value).hexdigest()[:20]}"


def relative_posix(path: Path, root: Path) -> str:
    return path.resolve(strict=False).relative_to(root.resolve(strict=False)).as_posix()


def _required_number(mapping: dict[str, Any], key: str, prefix: str) -> float:
    value = mapping[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{prefix}.{key} must be a finite number")
    return float(value)


def _required_positive_int(mapping: dict[str, Any], key: str, prefix: str) -> int:
    value = mapping[key]
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{prefix}.{key} must be a positive integer")
    return value


@dataclass(frozen=True)
class BuildRecord:
    id: str
    name: str
    package: str
    source_path: str
    texture_names: tuple[str, ...]
    atlas_paths: tuple[str, ...]
    symbols: frozenset[str]
    atlas_complete: bool
    xml_root: ET.Element
    structure_errors: tuple[str, ...]


@dataclass(frozen=True)
class ClipRecord:
    id: str
    name: str
    bank_name: str
    package: str
    source_path: str
    xml_index: int
    fps: float
    frame_count: int
    declared_frame_count: int
    symbols: frozenset[str]
    xml_node: ET.Element


class KleiDSTAdapter:
    """Build a bank-oriented manifest without guessing unavailable assets."""

    adapter_id = ADAPTER_ID

    def __init__(self, study_root: Path, config: dict[str, Any]) -> None:
        self.study_root = study_root.resolve(strict=True)
        self.config = config
        self.xml_root = self._resolve_config_path(config["xml_root"], "xml_root")
        self.atlas_root = self._resolve_config_path(config["atlas_root"], "atlas_root")
        matching = config["matching"]
        self.overlap_threshold = _required_number(
            matching, "symbol_overlap_threshold", "matching"
        )
        self.ready_coverage = _required_number(
            matching, "ready_symbol_coverage", "matching"
        )
        self.minimum_shared = _required_positive_int(
            matching, "minimum_shared_symbols", "matching"
        )
        self.maximum_candidates = _required_positive_int(
            matching, "maximum_candidates", "matching"
        )
        self.ambiguity_epsilon = _required_number(
            matching, "ambiguity_epsilon", "matching"
        )
        if not 0 <= self.overlap_threshold <= 1:
            raise ValueError("matching.symbol_overlap_threshold must be between 0 and 1")
        if not 0 <= self.ready_coverage <= 1:
            raise ValueError("matching.ready_symbol_coverage must be between 0 and 1")
        weights = matching["weights"]
        self.weights = {
            key: _required_number(weights, key, "matching.weights")
            for key in (
                "same_package",
                "build_name_equals_bank",
                "build_package_equals_bank",
                "symbol_coverage",
            )
        }
        self.render_policies = matching.get("render_policies", [])
        if not isinstance(self.render_policies, list):
            raise ValueError("matching.render_policies must be an array")
        for index, policy in enumerate(self.render_policies):
            prefix = f"matching.render_policies[{index}]"
            if not isinstance(policy, dict) or not str(policy.get("id", "")).strip():
                raise ValueError(f"{prefix}.id must be a non-empty string")
            if policy.get("missing_symbol_policy") not in {"strict", "skip"}:
                raise ValueError(f"{prefix}.missing_symbol_policy must be strict or skip")
            minimum = _required_number(policy, "minimum_symbol_coverage", prefix)
            if not 0 <= minimum <= 1:
                raise ValueError(f"{prefix}.minimum_symbol_coverage must be between 0 and 1")

    def _resolve_config_path(self, raw: Any, label: str) -> Path:
        if not isinstance(raw, str) or not raw:
            raise ValueError(f"{label} must be a non-empty study-root relative path")
        posix = PurePosixPath(raw)
        if posix.is_absolute() or ".." in posix.parts:
            raise ValueError(f"{label} must be a safe study-root relative path")
        candidate = (self.study_root / Path(*posix.parts)).resolve(strict=False)
        try:
            candidate.relative_to(self.study_root)
        except ValueError as error:
            raise ValueError(f"{label} escapes study-root") from error
        return candidate

    @staticmethod
    def _capabilities(renderable: bool, source_bundle: bool) -> dict[str, bool]:
        return {
            "current_frame_png": renderable,
            "png_sequence": renderable,
            "apng": renderable,
            "gif": renderable,
            "source_bundle": source_bundle,
        }

    @staticmethod
    def _area2(
        first: tuple[float, float],
        second: tuple[float, float],
        third: tuple[float, float],
    ) -> float:
        return (second[0] - first[0]) * (third[1] - first[1]) - (
            second[1] - first[1]
        ) * (third[0] - first[0])

    def _build_structure_errors(
        self, root: ET.Element, texture_names: tuple[str, ...]
    ) -> tuple[str, ...]:
        errors: set[str] = set()
        if not texture_names:
            errors.add("missing_texture_declaration")
        vertices: list[tuple[float, float, float, float, int]] = []
        for index, node in enumerate(root.findall("Alphavert")):
            try:
                x = float(node.attrib["x"])
                y = float(node.attrib["y"])
                u = float(node.attrib["u"])
                v = float(node.attrib["v"])
                raw_page = float(node.attrib.get("w", "0"))
                page = int(raw_page)
                if not all(math.isfinite(value) for value in (x, y, u, v, raw_page)):
                    raise ValueError
                if raw_page != page or page < 0 or page >= len(texture_names):
                    errors.add(f"invalid_atlas_page:{index}")
                vertices.append((x, y, u, v, page))
            except (KeyError, TypeError, ValueError, OverflowError):
                errors.add(f"invalid_alphavert:{index}")
                vertices.append((0.0, 0.0, 0.0, 0.0, -1))
        seen_symbols: set[str] = set()
        for symbol_index, symbol in enumerate(root.findall("Symbol")):
            name = symbol.attrib.get("name", "").strip()
            if not name or name in seen_symbols:
                errors.add(f"invalid_symbol_name:{symbol_index}")
            seen_symbols.add(name)
            seen_frames: set[int] = set()
            for frame_index, frame in enumerate(symbol.findall("Frame")):
                prefix = f"{name or symbol_index}:{frame_index}"
                try:
                    number = int(frame.attrib["framenum"])
                    duration = int(frame.attrib.get("duration", "1"))
                    alpha_index = int(frame.attrib["alphaidx"])
                    alpha_count = int(frame.attrib["alphacount"])
                except (KeyError, TypeError, ValueError, OverflowError):
                    errors.add(f"invalid_symbol_frame:{prefix}")
                    continue
                if number < 0 or number in seen_frames:
                    errors.add(f"invalid_symbol_frame_number:{prefix}")
                seen_frames.add(number)
                if duration <= 0 or alpha_index < 0 or alpha_count < 0 or alpha_count % 6:
                    errors.add(f"invalid_symbol_frame_range:{prefix}")
                    continue
                raw = vertices[alpha_index : alpha_index + alpha_count]
                if len(raw) != alpha_count:
                    errors.add(f"vertex_range_out_of_bounds:{prefix}")
                    continue
                for quad_index in range(0, len(raw), 6):
                    quad = raw[quad_index : quad_index + 6]
                    if len(quad) != 6:
                        errors.add(f"invalid_quad_vertex_count:{prefix}:{quad_index // 6}")
                        continue
                    pages = {quad[index][4] for index in (0, 1, 4, 2)}
                    if len(pages) != 1 or next(iter(pages)) < 0:
                        errors.add(f"invalid_quad_atlas_page:{prefix}:{quad_index // 6}")
                    if abs(self._area2(quad[0][:2], quad[1][:2], quad[2][:2])) < 1e-8:
                        errors.add(f"degenerate_quad_geometry:{prefix}:{quad_index // 6}")
                    uv = [(value[2], value[3]) for value in quad]
                    if abs(self._area2(uv[0], uv[1], uv[2])) < 1e-8:
                        errors.add(f"degenerate_quad_uv:{prefix}:{quad_index // 6}")
        return tuple(sorted(errors))

    def _parse_builds(self) -> list[BuildRecord]:
        builds: list[BuildRecord] = []
        if not self.xml_root.is_dir():
            return builds
        for source in sorted(self.xml_root.rglob("build.xml")):
            root = ET.parse(source).getroot()
            package = source.parent.relative_to(self.xml_root).as_posix()
            name = root.attrib.get("name", "").strip()
            if not name:
                continue
            texture_names = tuple(
                node.attrib["filename"]
                for node in root.findall("Texture")
                if node.attrib.get("filename")
            )
            atlas_paths = tuple(
                relative_posix(
                    self.atlas_root
                    / Path(*PurePosixPath(package).parts)
                    / Path(texture_name).with_suffix(".png").name,
                    self.study_root,
                )
                for texture_name in texture_names
            )
            atlas_complete = bool(atlas_paths) and all(
                (self.study_root / Path(*PurePosixPath(path).parts)).is_file()
                for path in atlas_paths
            )
            symbols = frozenset(
                node.attrib["name"]
                for node in root.findall("Symbol")
                if node.attrib.get("name")
            )
            builds.append(
                BuildRecord(
                    id=stable_id("build", package, name),
                    name=name,
                    package=package,
                    source_path=relative_posix(source, self.study_root),
                    texture_names=texture_names,
                    atlas_paths=atlas_paths,
                    symbols=symbols,
                    atlas_complete=atlas_complete,
                    xml_root=root,
                    structure_errors=self._build_structure_errors(root, texture_names),
                )
            )
        return builds

    def _parse_clips(self) -> list[ClipRecord]:
        clips: list[ClipRecord] = []
        if not self.xml_root.is_dir():
            return clips
        for source in sorted(self.xml_root.rglob("anim.xml")):
            package = source.parent.relative_to(self.xml_root).as_posix()
            root = ET.parse(source).getroot()
            for index, node in enumerate(root.findall("anim")):
                bank_name = node.attrib.get("root", "").strip()
                name = node.attrib.get("name", "").strip()
                if not bank_name or not name:
                    continue
                frames = node.findall("frame")
                symbols = frozenset(
                    element.attrib["name"]
                    for frame in frames
                    for element in frame.findall("element")
                    if element.attrib.get("name")
                )
                fps = float(node.attrib.get("framerate", "0"))
                declared = int(node.attrib.get("numframes", str(len(frames))))
                clips.append(
                    ClipRecord(
                        id=stable_id("clip", package, bank_name, name, str(index)),
                        name=name,
                        bank_name=bank_name,
                        package=package,
                        source_path=relative_posix(source, self.study_root),
                        xml_index=index,
                        fps=fps,
                        frame_count=len(frames),
                        declared_frame_count=declared,
                        symbols=symbols,
                        xml_node=node,
                    )
                )
        return clips

    def _render_policy(
        self,
        bank_name: str,
        package: str,
        build: BuildRecord,
        coverage: float,
    ) -> dict[str, Any] | None:
        bank_identity = bank_name.casefold()
        package_identity = package.casefold()
        for policy in self.render_policies:
            bank_names = {str(value).casefold() for value in policy.get("bank_names", [])}
            package_prefixes = tuple(
                str(value).casefold() for value in policy.get("package_prefixes", [])
            )
            if bank_names and bank_identity not in bank_names:
                continue
            if package_prefixes and not package_identity.startswith(package_prefixes):
                continue
            if policy.get("require_build_name_equals_bank") and (
                build.name.casefold() != bank_identity
            ):
                continue
            if coverage < float(policy["minimum_symbol_coverage"]):
                continue
            return policy
        return None

    def _clip_preview(
        self,
        clip: ClipRecord,
        build: BuildRecord | None,
        missing_symbol_policy: str = "strict",
        unavailable_reasons: Iterable[str] = (),
    ) -> dict[str, Any]:
        reasons = set(unavailable_reasons)
        if clip.fps <= 0 or not math.isfinite(clip.fps):
            reasons.add("invalid_fps")
        if clip.frame_count <= 0:
            reasons.add("no_animation_frames")
        partial = False
        if clip.declared_frame_count != clip.frame_count:
            reasons.add("declared_frame_count_mismatch")
            partial = True
        if build is None:
            status = "partial" if "ambiguous_build_candidates" in reasons else "blocked"
            reasons.add("default_build_unavailable")
            return {
                "preview_status": status,
                "capabilities": self._capabilities(False, False),
                "reasons": sorted(reasons),
            }
        source_bundle = build.atlas_complete
        if build.structure_errors:
            reasons.update(build.structure_errors)
        if not build.atlas_complete:
            reasons.add("atlas_incomplete")

        symbol_frames: dict[str, list[tuple[int, int, int, int]]] = {}
        vertices: list[tuple[float, float, float, float, int]] = []
        try:
            for node in build.xml_root.findall("Alphavert"):
                raw_page = float(node.attrib.get("w", "0"))
                vertices.append(
                    (
                        float(node.attrib["x"]),
                        float(node.attrib["y"]),
                        float(node.attrib["u"]),
                        float(node.attrib["v"]),
                        int(raw_page),
                    )
                )
            for symbol in build.xml_root.findall("Symbol"):
                name = symbol.attrib.get("name", "")
                symbol_frames[name.casefold()] = [
                    (
                        int(frame.attrib["framenum"]),
                        int(frame.attrib.get("duration", "1")),
                        int(frame.attrib["alphaidx"]),
                        int(frame.attrib["alphacount"]),
                    )
                    for frame in symbol.findall("Frame")
                ]
        except (KeyError, TypeError, ValueError, OverflowError):
            reasons.add("invalid_build_structure")

        try:
            animation = clip.xml_node
            if (
                animation.attrib.get("name") != clip.name
                or animation.attrib.get("root") != clip.bank_name
            ):
                raise IndexError
            empty_frames = 0
            skipped_symbols: set[str] = set()
            for frame_index, frame in enumerate(animation.findall("frame")):
                elements = frame.findall("element")
                if not elements:
                    empty_frames += 1
                    continue
                for element_index, element in enumerate(elements):
                    prefix = f"{frame_index}:{element_index}"
                    name = element.attrib.get("name", "")
                    try:
                        requested = int(element.attrib["frame"])
                        int(element.attrib["z_index"])
                        matrix = tuple(
                            float(element.attrib[key])
                            for key in ("m_a", "m_b", "m_c", "m_d", "m_tx", "m_ty")
                        )
                        if not all(math.isfinite(value) for value in matrix):
                            raise ValueError
                    except (KeyError, TypeError, ValueError, OverflowError):
                        reasons.add(f"invalid_element:{prefix}")
                        continue
                    frames = symbol_frames.get(name.casefold(), [])
                    duration = max((number + span for number, span, _start, _count in frames), default=0)
                    if duration <= 0:
                        if missing_symbol_policy == "skip":
                            skipped_symbols.add(name or prefix)
                            partial = True
                        else:
                            reasons.add(f"missing_symbol:{name or prefix}")
                        continue
                    held = requested % duration
                    selected = next(
                        (
                            value
                            for value in sorted(frames)
                            if value[0] <= held < value[0] + value[1]
                        ),
                        None,
                    )
                    if selected is None:
                        reasons.add(f"symbol_frame_gap:{name}:{held}")
                        continue
                    _number, _span, alpha_index, alpha_count = selected
                    raw = vertices[alpha_index : alpha_index + alpha_count]
                    if alpha_count == 0:
                        reasons.add(f"element_without_quad:{prefix}")
                        continue
                    if len(raw) != alpha_count or alpha_count % 6:
                        reasons.add(f"vertex_range_out_of_bounds:{name}:{held}")
                        continue
                    a, b, c, d, tx, ty = matrix
                    for quad_index in range(0, len(raw), 6):
                        quad = raw[quad_index : quad_index + 6]
                        corners = (quad[0], quad[1], quad[4], quad[2])
                        pages = {value[4] for value in corners}
                        if len(pages) != 1 or not 0 <= next(iter(pages)) < len(build.texture_names):
                            reasons.add(f"invalid_quad_atlas_page:{prefix}:{quad_index // 6}")
                        destination = [
                            (a * x + c * y + tx, b * x + d * y + ty)
                            for x, y, _u, _v, _page in corners
                        ]
                        if abs(self._area2(destination[0], destination[1], destination[3])) < 1e-8:
                            reasons.add(f"degenerate_destination_quad:{prefix}:{quad_index // 6}")
            if empty_frames:
                reasons.add(f"legal_empty_frames:{empty_frames}")
            if skipped_symbols:
                reasons.add(f"runtime_override_symbols_skipped:{len(skipped_symbols)}")
        except (ET.ParseError, IndexError, OSError):
            reasons.add("invalid_animation_structure")

        blocking_prefixes = (
            "atlas_incomplete",
            "degenerate_",
            "element_without_quad:",
            "invalid_",
            "missing_",
            "no_animation_frames",
            "symbol_frame_gap:",
            "vertex_range_out_of_bounds:",
        )
        blocking = any(reason.startswith(blocking_prefixes) for reason in reasons)
        renderable = not blocking
        if blocking:
            status = "blocked"
        elif partial:
            status = "partial"
        else:
            status = "ready"
            reasons.add("structure_preflight_passed")
        return {
            "preview_status": status,
            "capabilities": self._capabilities(renderable, source_bundle),
            "reasons": sorted(reasons),
        }

    def _candidate(
        self, build: BuildRecord, bank_name: str, package: str, symbols: frozenset[str]
    ) -> dict[str, Any] | None:
        normalized_symbols = {value.casefold() for value in symbols}
        normalized_build_symbols = {value.casefold() for value in build.symbols}
        shared = len(normalized_symbols & normalized_build_symbols)
        coverage = shared / len(symbols) if symbols else 1.0
        missing_symbols = sorted(
            value for value in symbols if value.casefold() not in normalized_build_symbols
        )
        reasons: list[str] = []
        score = coverage * self.weights["symbol_coverage"]
        if build.package == package:
            reasons.append("same_package")
            score += self.weights["same_package"]
        if build.name == bank_name:
            reasons.append("build_name_equals_bank")
            score += self.weights["build_name_equals_bank"]
        if build.package == bank_name:
            reasons.append("build_package_equals_bank")
            score += self.weights["build_package_equals_bank"]
        overlap_ok = shared >= self.minimum_shared and coverage >= self.overlap_threshold
        if overlap_ok:
            reasons.append("symbol_overlap")
        if not reasons:
            return None
        if reasons == ["symbol_overlap"] or any(reason != "symbol_overlap" for reason in reasons):
            if not overlap_ok and not any(
                reason in reasons
                for reason in (
                    "same_package",
                    "build_name_equals_bank",
                    "build_package_equals_bank",
                )
            ):
                return None
        preview_reasons: list[str] = []
        render_policy = self._render_policy(bank_name, package, build, coverage)
        missing_symbol_policy = (
            str(render_policy["missing_symbol_policy"]) if render_policy else "strict"
        )
        if build.structure_errors:
            preview_status = "blocked"
            preview_reasons.extend(build.structure_errors)
        elif not build.atlas_complete:
            preview_status = "blocked"
            preview_reasons.append("atlas_incomplete")
        elif coverage < self.ready_coverage:
            preview_status = "partial"
            preview_reasons.append("symbol_coverage_below_ready_threshold")
            if render_policy:
                preview_reasons.append(
                    f"render_policy:{render_policy['id']}"
                )
                preview_reasons.append("runtime_override_symbols_will_be_omitted")
        else:
            preview_status = "ready"
            preview_reasons.extend(("atlas_complete", "structure_preflight_passed"))
        return {
            "id": build.id,
            "build": {
                "name": build.name,
                "package": build.package,
                "source_path": build.source_path,
            },
            "score": round(score, 8),
            "symbol_coverage": round(coverage, 8),
            "shared_symbol_count": shared,
            "required_symbol_count": len(symbols),
            "missing_symbols": missing_symbols,
            "missing_symbol_policy": missing_symbol_policy,
            "atlas_complete": build.atlas_complete,
            "texture_names": list(build.texture_names),
            "atlases": list(build.atlas_paths),
            "reasons": reasons,
            "preview_status": preview_status,
            "capabilities": self._capabilities(
                preview_status == "ready"
                or (preview_status == "partial" and bool(render_policy)),
                build.atlas_complete,
            ),
            "preview_reasons": sorted(preview_reasons),
        }

    def _classify(self, bank_name: str, package: str, clips: Iterable[ClipRecord]) -> tuple[str, float, list[str]]:
        classification = self.config["classification"]
        identity = " ".join([bank_name, package]).casefold()
        all_text = " ".join(
            [bank_name, package, *(clip.name for clip in clips)]
        ).casefold()
        matches: list[tuple[float, float, int, str, list[str]]] = []
        for index, rule in enumerate(classification["rules"]):
            scope = str(rule.get("scope", "all"))
            if scope not in {"identity", "all"}:
                raise ValueError(f"classification.rules[{index}].scope must be identity or all")
            haystack = identity if scope == "identity" else all_text
            keywords = [str(value).casefold() for value in rule["keywords"]]
            found = sorted({keyword for keyword in keywords if keyword and keyword in haystack})
            if found:
                matches.append(
                    (
                        float(rule.get("priority", 0)),
                        float(rule["confidence"]),
                        -index,
                        str(rule["category"]),
                        found,
                    )
                )
        if not matches:
            return (
                str(classification["default_category"]),
                float(classification["default_confidence"]),
                ["classification_default"],
            )
        _priority, confidence, _negative_index, category, found = max(matches)
        return category, confidence, [f"keyword:{keyword}" for keyword in found]

    def build_manifest(self) -> dict[str, Any]:
        builds = self._parse_builds()
        clips = self._parse_clips()
        builds_by_package: dict[str, list[BuildRecord]] = defaultdict(list)
        builds_by_name: dict[str, list[BuildRecord]] = defaultdict(list)
        builds_by_symbol: dict[str, list[BuildRecord]] = defaultdict(list)
        builds_by_id: dict[str, BuildRecord] = {}
        for build in builds:
            builds_by_id[build.id] = build
            builds_by_package[build.package].append(build)
            builds_by_name[build.name].append(build)
            for symbol in build.symbols:
                builds_by_symbol[symbol].append(build)
        clips_by_bank: dict[tuple[str, str], list[ClipRecord]] = defaultdict(list)
        for clip in clips:
            clips_by_bank[(clip.package, clip.bank_name)].append(clip)

        items: list[dict[str, Any]] = []
        for (package, bank_name), bank_clips in sorted(clips_by_bank.items()):
            bank_symbols = frozenset(
                symbol for clip in bank_clips for symbol in clip.symbols
            )
            possible_builds: dict[str, BuildRecord] = {}
            for build in (
                *builds_by_package.get(package, ()),
                *builds_by_name.get(bank_name, ()),
                *builds_by_package.get(bank_name, ()),
            ):
                possible_builds[build.id] = build
            for symbol in bank_symbols:
                for build in builds_by_symbol.get(symbol, ()):
                    possible_builds[build.id] = build
            candidates = []
            for build in possible_builds.values():
                candidate = self._candidate(build, bank_name, package, bank_symbols)
                if candidate is not None:
                    candidates.append(candidate)
            candidates.sort(
                key=lambda value: (
                    -value["score"],
                    -value["symbol_coverage"],
                    value["build"]["package"],
                    value["build"]["name"],
                )
            )
            candidates = candidates[: self.maximum_candidates]
            ready = [
                value
                for value in candidates
                if value["preview_status"] == "ready"
            ]
            renderable = [
                value
                for value in candidates
                if value["capabilities"]["current_frame_png"]
            ]
            selectable = ready or renderable
            ambiguous = bool(
                len(selectable) > 1
                and abs(selectable[0]["score"] - selectable[1]["score"])
                <= self.ambiguity_epsilon
                and abs(
                    selectable[0]["symbol_coverage"]
                    - selectable[1]["symbol_coverage"]
                )
                <= self.ambiguity_epsilon
            )
            default_build_id = None if ambiguous or not selectable else selectable[0]["id"]
            selected_build = builds_by_id.get(default_build_id)
            selected_candidate = next(
                (value for value in candidates if value["id"] == default_build_id), None
            )
            warnings: list[str] = []
            errors: list[str] = []
            if ambiguous:
                warnings.append("ambiguous_build_candidates")
            if not candidates:
                errors.append("no_build_candidate")
            elif not any(value["atlas_complete"] for value in candidates):
                errors.append("no_candidate_with_complete_atlas")
            elif not ready:
                warnings.append("no_candidate_meets_ready_symbol_coverage")
            for clip in bank_clips:
                if clip.declared_frame_count != clip.frame_count:
                    warnings.append(f"frame_count_mismatch:{clip.id}")
                if clip.fps <= 0:
                    errors.append(f"invalid_fps:{clip.id}")
            unavailable_reasons: list[str] = []
            if ambiguous:
                unavailable_reasons.append("ambiguous_build_candidates")
            elif candidates and selected_build is None:
                unavailable_reasons.extend(candidates[0].get("preview_reasons", ()))
            elif not candidates:
                unavailable_reasons.append("no_build_candidate")
            clip_payloads: list[dict[str, Any]] = []
            for clip in bank_clips:
                preview = self._clip_preview(
                    clip,
                    selected_build,
                    (
                        selected_candidate.get("missing_symbol_policy", "strict")
                        if selected_candidate
                        else "strict"
                    ),
                    unavailable_reasons,
                )
                clip_payloads.append(
                    {
                        "id": clip.id,
                        "name": clip.name,
                        "fps": clip.fps,
                        "frame_count": clip.frame_count,
                        "declared_frame_count": clip.declared_frame_count,
                        "source_path": clip.source_path,
                        "xml_index": clip.xml_index,
                        "symbols": sorted(clip.symbols),
                        **preview,
                    }
                )
            if (
                selected_build is not None
                and clip_payloads
                and not errors
                and all(clip["preview_status"] == "ready" for clip in clip_payloads)
            ):
                preview_status = "ready"
            elif candidates or any(
                clip["preview_status"] != "blocked" for clip in clip_payloads
            ):
                preview_status = "partial"
            else:
                preview_status = "blocked"
            category, confidence, category_reasons = self._classify(
                bank_name, package, bank_clips
            )
            bank_source = bank_clips[0].source_path
            source_assets: list[dict[str, Any]] = [
                {"kind": "anim_xml", "path": bank_source}
            ]
            seen_assets = {bank_source}
            for candidate in candidates:
                build_path = candidate["build"]["source_path"]
                if build_path not in seen_assets:
                    source_assets.append({"kind": "build_xml", "path": build_path})
                    seen_assets.add(build_path)
                for atlas_path in candidate["atlases"]:
                    if atlas_path not in seen_assets:
                        source_assets.append({"kind": "atlas_png", "path": atlas_path})
                        seen_assets.add(atlas_path)
            items.append(
                {
                    "id": stable_id("bank", package, bank_name),
                    "adapter": ADAPTER_ID,
                    "category": category,
                    "confidence": confidence,
                    "reasons": category_reasons,
                    "bank": {
                        "name": bank_name,
                        "package": package,
                        "source_path": bank_source,
                    },
                    "build_candidates": candidates,
                    "default_build_id": default_build_id,
                    "clips": clip_payloads,
                    "source_assets": source_assets,
                    "capabilities": {
                        key: bool(clip_payloads)
                        and all(clip["capabilities"][key] for clip in clip_payloads)
                        for key in (
                            "current_frame_png",
                            "png_sequence",
                            "apng",
                            "gif",
                            "source_bundle",
                        )
                    },
                    "preview_status": preview_status,
                    "warnings": sorted(set(warnings)),
                    "errors": sorted(set(errors)),
                }
            )
        return {
            "schema_version": 1,
            "adapter": ADAPTER_ID,
            "study_root": ".",
            "source_roots": {
                "xml": relative_posix(self.xml_root, self.study_root),
                "atlas": relative_posix(self.atlas_root, self.study_root),
            },
            "item_count": len(items),
            "items": items,
        }
