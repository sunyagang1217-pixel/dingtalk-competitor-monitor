#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shlex
import shutil
import stat
import sys
import tempfile
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


SKILL_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_ROOT = SKILL_ROOT / "assets" / "project-template"
TEXT_SUFFIXES = {".md", ".py", ".sh", ".toml", ".txt"}
PLACEHOLDER_PATTERN = re.compile(r"__[A-Z0-9_]+__")
SLUG_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
ALLOWED_REGIONS = {"domestic", "international"}
REGION_LABELS = {"domestic": "国内", "international": "海外"}
WEEKDAY_LABELS = {
    1: "周一",
    2: "周二",
    3: "周三",
    4: "周四",
    5: "周五",
    6: "周六",
    7: "周日",
}


class SpecError(RuntimeError):
    """Raised when a project specification is unsafe or invalid."""


def _reject_credentials(value: Any, path: str = "规格") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).casefold().replace("-", "_")
            if (
                "webhook" in normalized
                or normalized in {"secret", "access_token", "dingtalk_secret"}
                or normalized.endswith("_secret")
            ):
                raise SpecError(
                    f"{path}包含凭据字段。不要把 Webhook 或加签密钥写入规格文件。"
                )
            _reject_credentials(child, f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _reject_credentials(child, f"{path}[{index}]")
        return
    if isinstance(value, str) and (
        "oapi.dingtalk.com/robot/send?access_token=" in value
        or re.fullmatch(r"SEC[A-Za-z0-9_-]{12,}", value.strip())
    ):
        raise SpecError(
            f"{path}疑似包含钉钉凭据。请删除并在生成项目后使用中文终端向导配置。"
        )


def _object(
    value: Any,
    label: str,
    *,
    required: set[str],
    allowed: set[str],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SpecError(f"{label}必须是 JSON 对象。")
    missing = sorted(required - value.keys())
    if missing:
        raise SpecError(f"{label}缺少字段：{', '.join(missing)}。")
    extra = sorted(value.keys() - allowed)
    if extra:
        raise SpecError(f"{label}包含不支持的字段：{', '.join(extra)}。")
    return value


def _text(value: Any, label: str, *, minimum: int = 1, maximum: int = 120) -> str:
    if not isinstance(value, str):
        raise SpecError(f"{label}必须是文本。")
    clean = value.strip()
    if any(character in clean for character in ("\n", "\r", "\x00")):
        raise SpecError(f"{label}不能包含换行或空字符。")
    if not minimum <= len(clean) <= maximum:
        raise SpecError(f"{label}长度必须在 {minimum} 至 {maximum} 个字符之间。")
    return clean


def _integer(
    value: Any,
    label: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not minimum <= value <= maximum
    ):
        raise SpecError(f"{label}必须是 {minimum} 至 {maximum} 的整数。")
    return value


def _slug(value: Any, label: str) -> str:
    slug = _text(value, label, maximum=63)
    if not SLUG_PATTERN.fullmatch(slug):
        raise SpecError(f"{label}只能包含小写字母、数字和单个连字符。")
    return slug


def _unique_text_list(
    value: Any,
    label: str,
    *,
    maximum_items: int = 10,
) -> list[str]:
    if not isinstance(value, list) or not value:
        raise SpecError(f"{label}必须是非空列表。")
    if len(value) > maximum_items:
        raise SpecError(f"{label}最多包含 {maximum_items} 项。")
    result = [_text(item, f"{label}[{index}]", maximum=100) for index, item in enumerate(value)]
    normalized = [item.casefold() for item in result]
    if len(normalized) != len(set(normalized)):
        raise SpecError(f"{label}不能包含重复项。")
    return result


def _weekday_description(weekdays: list[int]) -> str:
    if weekdays == [1, 2, 3, 4, 5, 6, 7]:
        return "每天"
    if weekdays == [1, 2, 3, 4, 5]:
        return "每周一至周五"
    return "每" + "、".join(WEEKDAY_LABELS[day] for day in weekdays)


def _cron_weekdays(weekdays: list[int]) -> str:
    return ",".join("0" if day == 7 else str(day) for day in weekdays)


def validate_spec(raw: Any) -> dict[str, Any]:
    _reject_credentials(raw)
    root = _object(
        raw,
        "规格",
        required={
            "project",
            "industry",
            "regions",
            "schedule",
            "digest",
            "competitors",
        },
        allowed={
            "project",
            "industry",
            "regions",
            "schedule",
            "digest",
            "competitors",
        },
    )

    project = _object(
        root["project"],
        "project",
        required={"slug", "display_name"},
        allowed={"slug", "display_name"},
    )
    project_slug = _slug(project["slug"], "project.slug")
    project_display_name = _text(
        project["display_name"],
        "project.display_name",
        minimum=2,
        maximum=80,
    )

    industry = _object(
        root["industry"],
        "industry",
        required={"name", "digest_title"},
        allowed={"name", "digest_title"},
    )
    industry_name = _text(
        industry["name"],
        "industry.name",
        minimum=2,
        maximum=80,
    )
    digest_title = _text(
        industry["digest_title"],
        "industry.digest_title",
        minimum=2,
        maximum=80,
    )

    regions_raw = root["regions"]
    if not isinstance(regions_raw, list) or not regions_raw:
        raise SpecError("regions 必须是非空列表。")
    if any(region not in ALLOWED_REGIONS for region in regions_raw):
        raise SpecError("regions 只支持 domestic 和 international。")
    if len(regions_raw) != len(set(regions_raw)):
        raise SpecError("regions 不能包含重复项。")
    regions = [region for region in ("domestic", "international") if region in regions_raw]

    schedule = _object(
        root["schedule"],
        "schedule",
        required={"timezone", "time", "weekdays"},
        allowed={"timezone", "time", "weekdays"},
    )
    timezone = _text(schedule["timezone"], "schedule.timezone", maximum=80)
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise SpecError(f"schedule.timezone 无法识别：{timezone}。") from exc
    send_time = _text(schedule["time"], "schedule.time", maximum=5)
    time_match = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", send_time)
    if not time_match:
        raise SpecError("schedule.time 必须使用 24 小时制 HH:MM，例如 10:30。")
    weekdays_raw = schedule["weekdays"]
    if not isinstance(weekdays_raw, list) or not weekdays_raw:
        raise SpecError("schedule.weekdays 必须是非空列表。")
    weekdays = sorted(
        _integer(day, f"schedule.weekdays[{index}]", minimum=1, maximum=7)
        for index, day in enumerate(weekdays_raw)
    )
    if len(weekdays) != len(set(weekdays)):
        raise SpecError("schedule.weekdays 不能包含重复项。")

    digest = _object(
        root["digest"],
        "digest",
        required={
            "format",
            "max_items",
            "lookback_days",
            "preferred_domestic_items",
            "preferred_international_items",
        },
        allowed={
            "format",
            "max_items",
            "lookback_days",
            "preferred_domestic_items",
            "preferred_international_items",
        },
    )
    digest_format = _text(digest["format"], "digest.format", maximum=20)
    if digest_format not in {"analysis", "brief"}:
        raise SpecError("digest.format 必须是 analysis 或 brief。")
    max_items = _integer(digest["max_items"], "digest.max_items", minimum=1, maximum=20)
    lookback_days = _integer(
        digest["lookback_days"],
        "digest.lookback_days",
        minimum=1,
        maximum=30,
    )
    preferred_domestic = _integer(
        digest["preferred_domestic_items"],
        "digest.preferred_domestic_items",
        minimum=0,
        maximum=20,
    )
    preferred_international = _integer(
        digest["preferred_international_items"],
        "digest.preferred_international_items",
        minimum=0,
        maximum=20,
    )
    if preferred_domestic + preferred_international != max_items:
        raise SpecError("国内与海外优先条数之和必须等于 digest.max_items。")
    if "domestic" not in regions and preferred_domestic != 0:
        raise SpecError("未监控国内地区时，国内优先条数必须为 0。")
    if "international" not in regions and preferred_international != 0:
        raise SpecError("未监控海外地区时，海外优先条数必须为 0。")

    competitors_raw = root["competitors"]
    if not isinstance(competitors_raw, list) or not competitors_raw:
        raise SpecError("competitors 必须是非空列表。")
    if len(competitors_raw) > 100:
        raise SpecError("competitors 最多包含 100 个竞品。")
    competitors: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    for index, value in enumerate(competitors_raw):
        label = f"competitors[{index}]"
        item = _object(
            value,
            label,
            required={"id", "name", "region", "priority", "aliases", "query"},
            allowed={"id", "name", "region", "priority", "aliases", "query"},
        )
        competitor_id = _slug(item["id"], f"{label}.id")
        name = _text(item["name"], f"{label}.name", maximum=100)
        region = item["region"]
        if region not in regions:
            raise SpecError(f"{label}.region 不在已选择的监控地区中。")
        priority = _integer(
            item["priority"],
            f"{label}.priority",
            minimum=1,
            maximum=5,
        )
        aliases = _unique_text_list(item["aliases"], f"{label}.aliases")
        if name.casefold() not in {alias.casefold() for alias in aliases}:
            raise SpecError(f"{label}.aliases 必须包含竞品正式名称。")
        query = _text(item["query"], f"{label}.query", maximum=300)
        if not any(alias.casefold() in query.casefold() for alias in aliases):
            raise SpecError(f"{label}.query 必须包含至少一个竞品别名。")
        if competitor_id in seen_ids:
            raise SpecError(f"竞品 id 重复：{competitor_id}。")
        if name.casefold() in seen_names:
            raise SpecError(f"竞品名称重复：{name}。")
        seen_ids.add(competitor_id)
        seen_names.add(name.casefold())
        competitors.append(
            {
                "id": competitor_id,
                "name": name,
                "region": region,
                "priority": priority,
                "aliases": aliases,
                "query": query,
            }
        )

    covered_regions = {item["region"] for item in competitors}
    missing_regions = [REGION_LABELS[region] for region in regions if region not in covered_regions]
    if missing_regions:
        raise SpecError(f"以下监控地区还没有竞品：{'、'.join(missing_regions)}。")

    hour, minute = time_match.groups()
    return {
        "project": {"slug": project_slug, "display_name": project_display_name},
        "industry": {"name": industry_name, "digest_title": digest_title},
        "regions": regions,
        "schedule": {
            "timezone": timezone,
            "time": send_time,
            "weekdays": weekdays,
            "cron": f"{int(minute)} {int(hour)} * * {_cron_weekdays(weekdays)}",
        },
        "digest": {
            "format": digest_format,
            "max_items": max_items,
            "lookback_days": lookback_days,
            "preferred_domestic_items": preferred_domestic,
            "preferred_international_items": preferred_international,
        },
        "competitors": competitors,
    }


def _replacement_values(spec: dict[str, Any]) -> dict[str, str]:
    digest_format = spec["digest"]["format"]
    format_label = "分析版" if digest_format == "analysis" else "快讯版"
    if digest_format == "analysis":
        format_guidance = (
            "`impact` 用 1 句中文说明对本行业值得关注的产品、服务、用户、"
            "渠道、技术或商业影响；推断必须使用“可能”“值得关注”等措辞。"
        )
    else:
        format_guidance = (
            "快讯版允许 `impact` 保持空字符串；`fact_summary` 只概括原文可核验事实，"
            "不追加未经来源支持的影响判断。"
        )
    competitors_by_region = {
        region: [item["name"] for item in spec["competitors"] if item["region"] == region]
        for region in spec["regions"]
    }
    competitor_lines = [
        f"  - {REGION_LABELS[region]}：{'、'.join(competitors_by_region[region])}"
        for region in spec["regions"]
    ]
    return {
        "__PROJECT_SLUG__": spec["project"]["slug"],
        "__PROJECT_DISPLAY_NAME__": spec["project"]["display_name"],
        "__INDUSTRY_NAME__": spec["industry"]["name"],
        "__DIGEST_TITLE__": spec["industry"]["digest_title"],
        "__REGION_SCOPE__": "、".join(REGION_LABELS[item] for item in spec["regions"]),
        "__COMPETITOR_COUNT__": str(len(spec["competitors"])),
        "__COMPETITOR_LIST__": "\n".join(competitor_lines),
        "__SCHEDULE_DESCRIPTION__": (
            f"{spec['schedule']['timezone']} 时区，"
            f"{_weekday_description(spec['schedule']['weekdays'])} "
            f"{spec['schedule']['time']}"
        ),
        "__LOOKBACK_DAYS__": str(spec["digest"]["lookback_days"]),
        "__DIGEST_FORMAT_LABEL__": format_label,
        "__DIGEST_FORMAT_GUIDANCE__": format_guidance,
        "__MAX_ITEMS__": str(spec["digest"]["max_items"]),
    }


def _write_monitoring_config(project_root: Path, spec: dict[str, Any]) -> None:
    monitoring = {
        "industry": {
            "name": spec["industry"]["name"],
            "digest_title": spec["industry"]["digest_title"],
        },
        "schedule": {
            "timezone": spec["schedule"]["timezone"],
            "cron": spec["schedule"]["cron"],
            "weekdays": spec["schedule"]["weekdays"],
        },
        "digest": spec["digest"],
        "competitors": spec["competitors"],
    }
    config_path = project_root / "config" / "monitoring.json"
    config_path.write_text(
        json.dumps(monitoring, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _render_project(project_root: Path, spec: dict[str, Any]) -> None:
    replacements = _replacement_values(spec)
    for path in project_root.rglob("*"):
        if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
            continue
        content = path.read_text(encoding="utf-8")
        for placeholder, replacement in replacements.items():
            content = content.replace(placeholder, replacement)
        path.write_text(content, encoding="utf-8")
    _write_monitoring_config(project_root, spec)

    unresolved: list[str] = []
    for path in project_root.rglob("*"):
        if not path.is_file() or (
            path.suffix not in TEXT_SUFFIXES and path.suffix != ".json"
        ):
            continue
        matches = PLACEHOLDER_PATTERN.findall(path.read_text(encoding="utf-8"))
        if matches:
            unresolved.append(f"{path.relative_to(project_root)}: {', '.join(matches)}")
    if unresolved:
        raise SpecError("项目模板仍有未替换字段：" + "; ".join(unresolved))

    for script_name in ("configure_dingtalk.py", "configure_dingtalk.sh"):
        script_path = project_root / "scripts" / script_name
        script_path.chmod(
            script_path.stat().st_mode
            | stat.S_IXUSR
            | stat.S_IXGRP
            | stat.S_IXOTH
        )


def _prepare_output(output_arg: str) -> Path:
    raw_output = Path(output_arg).expanduser()
    if raw_output.is_symlink():
        raise SpecError("输出路径不能是符号链接。")
    output = raw_output.resolve(strict=False)
    if output in {Path("/"), Path.home().resolve()}:
        raise SpecError("输出路径不能是文件系统根目录或用户主目录。")
    template = TEMPLATE_ROOT.resolve()
    if output.is_relative_to(template) or template.is_relative_to(output):
        raise SpecError("输出路径不能与 Skill 项目模板重叠。")
    if output.exists():
        if not output.is_dir():
            raise SpecError("输出路径已存在，但不是目录。")
        if any(output.iterdir()):
            raise SpecError("输出目录不是空目录；为保护现有文件，已停止生成。")
    return output


def scaffold(spec: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{spec['project']['slug']}-staging-",
            dir=output.parent,
        )
    )
    try:
        shutil.copytree(TEMPLATE_ROOT, staging, dirs_exist_ok=True)
        _render_project(staging, spec)
        if output.exists():
            output.rmdir()
        staging.replace(output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="根据不含凭据的 JSON 规格生成行业竞品监控钉钉机器人项目。"
    )
    parser.add_argument("--spec", required=True, help="JSON 规格文件路径。")
    parser.add_argument("--output", required=True, help="新的空项目目录。")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        spec_path = Path(args.spec).expanduser()
        try:
            raw = json.loads(spec_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SpecError(f"无法读取 JSON 规格文件：{spec_path}。") from exc
        spec = validate_spec(raw)
        output = _prepare_output(args.output)
        scaffold(spec, output)
    except SpecError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    print("[完成] 行业竞品监控机器人项目已生成。")
    print(f"项目目录：{output}")
    print(f"行业：{spec['industry']['name']}")
    print(f"竞品数量：{len(spec['competitors'])}")
    print(
        "运行时间："
        f"{_weekday_description(spec['schedule']['weekdays'])} "
        f"{spec['schedule']['time']}（{spec['schedule']['timezone']}）"
    )
    print("\n下一步请在终端依次运行：")
    print(f"cd {shlex.quote(str(output))}")
    print("python3 -m venv .venv")
    print(".venv/bin/python -m pip install -e .")
    print("./scripts/configure_dingtalk.sh")
    print("\n配置向导不会发送消息；完成后回到 Codex 确认测试内容。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
