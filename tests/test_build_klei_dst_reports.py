from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from build_klei_dst_reports import build_reports  # noqa: E402


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_builds_full_klei_dst_report_set(tmp_path: Path) -> None:
    study = tmp_path / "DST Study"
    for name in ["0.原始导出", "1.分类资源", "2.报告", "3.代码", "4.临时目录"]:
        (study / name).mkdir(parents=True)

    index = study / "4.临时目录" / "中间索引"
    reports = study / "2.报告"
    scripts = study / "3.代码" / "脚本" / "packages" / "data" / "databundles" / "scripts" / "scripts"
    image = study / "1.分类资源" / "图片" / "图集" / "KleiTEX" / "data" / "anim" / "wilson" / "atlas-0.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(PNG_1X1)
    audio_dir = study / "1.分类资源" / "音频" / "KleiFSB" / "sfx"
    write_text(audio_dir / "0000_hit.ogg", "fake audio")
    write_text(audio_dir / "0001_walk.ogg", "fake audio")
    write_text(audio_dir / "0002_extra.wav", "fake audio")
    write_text(scripts / "actions.lua", "-- sample")
    write_text(scripts / "prefabs" / "wilson.lua", "-- sample")

    write_text(
        index / "KleiDST" / "klei_dst_export_summary.json",
        json.dumps(
            {
                "source": "E:/Games/DST",
                "study_root": str(study),
                "project_name": "Don't Starve Together",
                "packages": 3,
                "code_files": 2,
                "atlas_or_texture_records": 1,
                "audio_records": 1,
                "dyn_records": 1,
                "top_extensions": [[".zip", 3], [".dyn", 1]],
                "package_parent_counts": [["anim", 2], ["databundles", 1]],
                "status": "DONE_WITH_CONCERNS",
            },
            ensure_ascii=False,
        ),
    )
    write_text(
        study / "4.临时目录" / "工具日志" / "klei_resource_conversion_summary.json",
        json.dumps(
            {
                "textures": {"total": 1, "converted": 1, "failed": 0},
                "animations": {"total": 1, "converted": 1, "failed": 0},
                "audio": {
                    "banks_total": 1,
                    "banks_converted": 1,
                    "banks_failed": 0,
                    "samples_converted": 3,
                    "samples_failed": 0,
                },
            },
            ensure_ascii=False,
        ),
    )
    write_text(
        index / "学习输出" / "klei_texture_conversion_index.csv",
        "source,output,status,message\nraw.tex,out.png,converted,ok\n",
    )
    write_text(
        index / "学习输出" / "klei_animation_xml_index.csv",
        "source,output,status,message\ndata/anim/wilson,out,converted,build_exit=0 | anim_exit=0\n",
    )
    write_text(
        index / "学习输出" / "klei_fsb_conversion_index.csv",
        "bank,sample_index,sample_name,output,status\nsfx.fsb,0,hit,out.ogg,converted\nsfx.fsb,1,walk,out.ogg,converted\n",
    )
    write_text(
        index / "KleiDST" / "代码导出索引.csv",
        "source,output,size,status\nscripts/actions.lua,out,100,copied_from_package\n",
    )
    write_text(
        index / "KleiDST" / "动态动画阻塞索引.csv",
        "source,raw_output,size,magic16,status,note\ndata/anim/dynamic/wilson.dyn,out,10,9394,raw_copied_conversion_blocked,not zip\n",
    )
    write_text(
        index / "KleiDST" / "dyn_boundary_inventory_summary.json",
        json.dumps(
            {
                "dyn_files": 1,
                "anim_dynamic_zip_names": 1,
                "matched_anim_dynamic_zip": 1,
                "unmatched_anim_dynamic_zip": 0,
                "entropy64_avg": 5.1,
                "exe_string_hits": [{"exe": "bin64/game.exe", "string": "DYNAMIC_ANIM"}],
            },
            ensure_ascii=False,
        ),
    )

    result = build_reports(study)

    assert result["reports_written"] == 10
    assert (reports / "00_研究总览.md").exists()
    assert (reports / "09_可迁移技术清单.md").exists()
    overview = (reports / "00_研究总览.md").read_text(encoding="utf-8")
    assert "Skill 迭代" in overview
    assert "| 2.报告 | 10 |" in overview
    assert "converted" in (reports / "03_动画与手感帧数据.md").read_text(encoding="utf-8")
    gameplay = (reports / "06_玩法系统拆解.md").read_text(encoding="utf-8")
    assert "音频样本文件 3 个" in gameplay
    assert "转换摘要 samples_converted=3" in gameplay
    assert "音频转换索引 2 行" in gameplay
    assert "音频样本 2 条转换索引" not in gameplay
    for report_name in ["08_命名规范汇总.md", "09_可迁移技术清单.md"]:
        report = (reports / report_name).read_text(encoding="utf-8")
        assert "## 观察到的事实" in report
        assert "## 我的判断" in report
    assert (study / "3.代码" / "代码可读性判定.md").exists()
    blocker = (study / "4.临时目录" / "失败记录" / "KleiDST剩余阻塞.md")
    assert blocker.exists()
    assert "同名 ZIP 对照：1 / 1" in blocker.read_text(encoding="utf-8")
