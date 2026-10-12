from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VIEWER = ROOT / "assets" / "model-gallery-viewer-src"


def test_model_viewer_has_animation_playback_controls() -> None:
    html = (VIEWER / "index.html").read_text(encoding="utf-8")
    renderer = (VIEWER / "src" / "renderer.js").read_text(encoding="utf-8")
    styles = (VIEWER / "src" / "styles.css").read_text(encoding="utf-8")

    for element_id in [
        "animation-panel",
        "animation-sidebar",
        "animation-list",
        "animation-count",
        "animation-current-name",
        "animation-select",
        "animation-toggle",
        "animation-timeline",
        "animation-speed",
        "animation-status",
    ]:
        assert f'id="{element_id}"' in html

    assert "new THREE.AnimationMixer" in renderer
    assert "gltf.animations" in renderer
    assert "clipAction" in renderer
    assert "animationMixer.update" in renderer
    assert "syncAnimationTimeline" in renderer
    assert "toggleAnimationPlayback" in renderer
    assert "renderAnimationList" in renderer
    assert "pauseAnimationPlayback" in renderer
    assert ".animation-panel" in styles
    assert ".animation-sidebar" in styles


def test_model_viewer_layout_keeps_controls_outside_viewport() -> None:
    html = (VIEWER / "index.html").read_text(encoding="utf-8")
    styles = (VIEWER / "src" / "styles.css").read_text(encoding="utf-8")

    assert html.index('class="viewer-pane"') < html.index('id="animation-sidebar"')
    assert html.index('id="animation-sidebar"') < html.index('id="animation-panel"')
    assert ".preview-overlay.has-animations .preview-stage" in styles
    assert "grid-template-areas:" in styles
    assert "grid-area: viewer;" in styles
    assert "grid-area: sidebar;" in styles
    assert "grid-area: controls;" in styles

    panel_block = styles.split(".animation-panel {", 1)[1].split("}", 1)[0]
    assert "position: absolute" not in panel_block
    assert "grid-area: controls;" in panel_block


def test_timeline_scrub_pauses_until_play_button_clicked() -> None:
    renderer = (VIEWER / "src" / "renderer.js").read_text(encoding="utf-8")

    assert "function pauseAnimationPlayback()" in renderer
    assert 'animationTimeline.addEventListener("pointerdown", pauseAnimationPlayback)' in renderer
    assert 'animationTimeline.addEventListener("input", () => {' in renderer
    assert "pauseAnimationPlayback();" in renderer


def test_model_viewer_meta_and_cards_surface_animation_status() -> None:
    renderer = (VIEWER / "src" / "renderer.js").read_text(encoding="utf-8")

    assert "renderAnimationSummary" in renderer
    assert "animation_status" in renderer
    assert "animation_origin" in renderer
    assert "procedural_preview" in renderer
    assert "animation:${item.animation_status}" in renderer
    assert "程序化动作 ${item.stats?.animation_count || 0}" in renderer


def test_model_viewer_exposes_full_body_preview_button_and_composite_loader() -> None:
    html = (VIEWER / "index.html").read_text(encoding="utf-8")
    renderer = (VIEWER / "src" / "renderer.js").read_text(encoding="utf-8")
    styles = (VIEWER / "src" / "styles.css").read_text(encoding="utf-8")

    assert 'id="full-body-preview"' in html
    assert "renderFullBodyPreviewAction" in renderer
    assert "openFullBodyPreview" in renderer
    assert "loadFullBodyPreview" in renderer
    assert "full_body_preview" in renderer
    assert "new THREE.Group" in renderer
    assert ".full-body-preview-button" in styles
