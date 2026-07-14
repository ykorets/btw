"""Build a deterministic BTW Weekly edition and request editor review.

The newsletter is a view of the published record, not a second research
pipeline.  It reads exact rows promoted through sealed review manifests,
plus separately labelled published facility and third-party announcement
updates.  Candidates and staging facts are intentionally excluded.

The Monday workflow creates a Resend draft and emails a preview only to the
editor.  The Worker owns the Resend key, approval gate and delivery cursor.
Nothing reaches the subscriber segment until the editor confirms with a POST
action.  JSON, Markdown and HTML preview artifacts are written for audit.

Env: SUPABASE_URL, SUPABASE_SERVICE_KEY; for --request-review also
DIGEST_TRIGGER_SECRET.  Optional: NEWSLETTER_ENDPOINT.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from btw_engine.review import _rest

SITE_ORIGIN = "https://behindthewatt.com"
DEFAULT_ENDPOINT = "https://newsletter.behindthewatt.com/broadcast"
UNSUBSCRIBE_URL = "{{{RESEND_UNSUBSCRIBE_URL}}}"
ISSUE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _datetime(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _get(path: str, **params: str) -> list[dict[str, Any]]:
    return _rest("GET", path, params=params).json()


def _in(values: list[str]) -> str:
    return "in.(" + ",".join(values) + ")"


def _valid_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlparse(value)
    return value if parsed.scheme == "https" and parsed.netloc else None


def _attach_sources(rows: list[dict[str, Any]], fact_table: str) -> None:
    ids = [str(row["id"]) for row in rows if row.get("id")]
    if not ids:
        return
    provenance = _get(
        "fact_provenance",
        select=(
            "fact_id,fact_field,support_kind,derivation,"
            "claim:claim_id(document:document_id(url,sha256,r2_key))"
        ),
        fact_table=f"eq.{fact_table}",
        fact_id=_in(ids),
    )
    by_id: dict[str, list[str]] = {}
    for item in provenance:
        claim = item.get("claim") or {}
        document = claim.get("document") or {}
        url = _valid_url(document.get("url"))
        if url:
            urls = by_id.setdefault(str(item["fact_id"]), [])
            if url not in urls:
                urls.append(url)
    for row in rows:
        row["sources"] = by_id.get(str(row.get("id")), [])


def _manifest_rows(
    start: dt.datetime, end: dt.datetime
) -> tuple[list[dict[str, Any]], list[dict[str, Any]],
           list[dict[str, Any]], list[dict[str, Any]]]:
    reviews = _get(
        "review",
        select="id,batch_date,promoted_at,manifest_hash,merge_commit_sha",
        decision="eq.promoted",
        promoted_at=f"gte.{_iso(start)}",
    )
    # PostgREST cannot receive two values for one keyword. Apply the upper
    # bound locally as a defensive fallback; scheduled windows are tiny.
    reviews = [
        row for row in reviews
        if row.get("promoted_at") and _datetime(row["promoted_at"]) < end
    ]
    review_ids = [str(row["id"]) for row in reviews]
    if not review_ids:
        return reviews, [], [], []

    items = _get(
        "review_manifest_item",
        select="review_id,unit_id,permit_id,event_id",
        review_id=_in(review_ids),
    )
    unit_ids = [str(row["unit_id"]) for row in items if row.get("unit_id")]
    permit_ids = [str(row["permit_id"]) for row in items if row.get("permit_id")]
    event_ids = [str(row["event_id"]) for row in items if row.get("event_id")]

    units = _get(
        "unit",
        select=(
            "id,logical_id,oem,model,unit_count,mw_each,total_mw,fuel,"
            "hours_permitted,basis,verification_state,facility(slug,name)"
        ),
        id=_in(unit_ids),
        fact_state="eq.published",
        order="facility_id",
    ) if unit_ids else []
    permits = _get(
        "permit",
        select=(
            "id,logical_id,authority,permit_no,permit_type,status,filed_at,"
            "issued_at,verification_state,facility(slug,name)"
        ),
        id=_in(permit_ids),
        fact_state="eq.published",
        order="facility_id",
    ) if permit_ids else []
    events = _get(
        "event",
        select=(
            "id,event_date,event_type,headline,source_url,facility(slug,name)"
        ),
        id=_in(event_ids),
        fact_state="eq.published",
        order="event_date.desc",
    ) if event_ids else []

    counts: dict[str, dict[str, int]] = {
        review_id: {"units": 0, "permits": 0, "events": 0}
        for review_id in review_ids
    }
    for item in items:
        bucket = counts[str(item["review_id"])]
        bucket["units"] += int(item.get("unit_id") is not None)
        bucket["permits"] += int(item.get("permit_id") is not None)
        bucket["events"] += int(item.get("event_id") is not None)
    for review in reviews:
        review["manifest_counts"] = counts[str(review["id"])]

    _attach_sources(units, "unit")
    _attach_sources(permits, "permit")
    return reviews, units, permits, events


def gather(start: dt.datetime, end: dt.datetime) -> dict[str, Any]:
    """Read only published rows for the half-open edition window."""
    reviews, units, permits, events = _manifest_rows(start, end)

    facilities = _get(
        "facility",
        select=(
            "id,slug,name,state,status,developer,offtaker,first_permit_filed,"
            "first_power,updated_at"
        ),
        fact_state="eq.published",
        updated_at=f"gte.{_iso(start)}",
        order="updated_at,name",
    )
    facilities = [
        row for row in facilities
        if row.get("updated_at") and _datetime(row["updated_at"]) < end
    ]
    _attach_sources(facilities, "facility")

    announcements = _get(
        "announcement",
        select=(
            "id,name,state,county,project_type,operating_status,"
            "expected_operating_year,generating_technology,"
            "reported_capacity_mw,source_as_of,updated_at,"
            "source_document:document!announcement_source_document_id_fkey(url)"
        ),
        fact_state="eq.published",
        updated_at=f"gte.{_iso(start)}",
        order="updated_at,name",
    )
    announcements = [
        row for row in announcements
        if row.get("updated_at") and _datetime(row["updated_at"]) < end
    ]

    latest = _get(
        "aggregate",
        select="value,computed_at,method,inputs_note",
        metric="eq.operating_gw",
        computed_at=f"lt.{_iso(end)}",
        order="computed_at.desc",
        limit="1",
    )
    baseline = _get(
        "aggregate",
        select="value,computed_at",
        metric="eq.operating_gw",
        computed_at=f"lte.{_iso(start)}",
        order="computed_at.desc",
        limit="1",
    )
    current_gw = float(latest[0]["value"]) if latest else 0.0
    baseline_gw = float(baseline[0]["value"]) if baseline else None

    current_facilities = _get(
        "facility", select="id", fact_state="eq.published"
    )
    current_announcements = _get(
        "announcement",
        select="reported_capacity_mw",
        fact_state="eq.published",
    )
    reported_mw = sum(
        float(row["reported_capacity_mw"])
        for row in current_announcements
        if row.get("reported_capacity_mw") is not None
    )

    return {
        "reviews": reviews,
        "units": units,
        "permits": permits,
        "events": events,
        "facilities": facilities,
        "announcements": announcements,
        "snapshot": {
            "operating_gw": current_gw,
            "baseline_operating_gw": baseline_gw,
            "operating_delta_mw": (
                round((current_gw - baseline_gw) * 1000, 1)
                if baseline_gw is not None else None
            ),
            "published_facilities": len(current_facilities),
            "reported_projects": len(current_announcements),
            "third_party_reported_gw": round(reported_mw / 1000, 1),
            "aggregate_computed_at": latest[0]["computed_at"] if latest else None,
        },
    }


def _facility(row: dict[str, Any]) -> dict[str, Any]:
    return row.get("facility") or {}


def _dossier_url(row: dict[str, Any]) -> str:
    slug = _facility(row).get("slug") or row.get("slug")
    return f"{SITE_ORIGIN}/facility/{slug}/" if slug else f"{SITE_ORIGIN}/#data"


def _mw(row: dict[str, Any]) -> float | None:
    if row.get("total_mw") is not None:
        return float(row["total_mw"])
    if row.get("unit_count") is not None and row.get("mw_each") is not None:
        return float(row["unit_count"]) * float(row["mw_each"])
    return None


def _unit_label(row: dict[str, Any]) -> str:
    count = f"{row['unit_count']}×" if row.get("unit_count") is not None else "count unknown"
    return " ".join(value for value in (
        count, row.get("oem") or "unknown OEM", row.get("model") or ""
    ) if value)


def _number(value: float | int | None) -> str:
    if value is None:
        return "not stated"
    return f"{value:,.1f}".rstrip("0").rstrip(".")


def _meaning(data: dict[str, Any]) -> str:
    snapshot = data["snapshot"]
    change_count = sum(len(data[key]) for key in (
        "units", "permits", "events", "facilities"
    ))
    delta = snapshot["operating_delta_mw"]
    if delta is None:
        return (
            f"The verified operating record stands at "
            f"{snapshot['operating_gw']:.2f} GW. This is the first automated "
            "edition, so no like-for-like weekly capacity delta is asserted."
        )
    if delta > 0:
        return (
            f"Verified operating capacity rose by {_number(delta)} MW to "
            f"{snapshot['operating_gw']:.2f} GW across the edition window."
        )
    if delta < 0:
        return (
            f"Verified operating capacity fell by {_number(abs(delta))} MW to "
            f"{snapshot['operating_gw']:.2f} GW as the published record was corrected."
        )
    if change_count:
        return (
            "The verified operating total did not change. This edition's "
            "updates concern equipment, permits, facility metadata or the event record."
        )
    return (
        "No sealed review manifest was promoted in this edition window; the "
        f"verified operating record remains {snapshot['operating_gw']:.2f} GW."
    )


def _subject(issue_id: str, data: dict[str, Any]) -> str:
    verified = sum(len(data[key]) for key in (
        "units", "permits", "events", "facilities"
    ))
    reported = len(data["announcements"])
    if verified:
        suffix = "change" if verified == 1 else "changes"
        return f"BTW Weekly — {issue_id}: {verified} published record {suffix}"
    if reported:
        suffix = "update" if reported == 1 else "updates"
        return f"BTW Weekly — {issue_id}: {reported} reported-project {suffix}"
    return f"BTW Weekly — {issue_id}: no new published record changes"


def _source_suffix(row: dict[str, Any]) -> str:
    urls = row.get("sources") or []
    if not urls:
        direct = _valid_url(row.get("source_url"))
        urls = [direct] if direct else []
    return " ".join(f"[source {index + 1}]({url})" for index, url in enumerate(urls))


def render_markdown(issue_id: str, start: dt.datetime, end: dt.datetime,
                    data: dict[str, Any]) -> str:
    snapshot = data["snapshot"]
    lines = [
        f"# BTW Weekly — {issue_id}",
        "",
        f"_Published-record window: {_iso(start)} to {_iso(end)}._",
        "",
        _meaning(data),
        "",
        "## At a glance",
        "",
        f"- Verified operating record: **{snapshot['operating_gw']:.2f} GW**",
        f"- Published facilities: **{snapshot['published_facilities']}**",
        f"- Third-party inventory: **{snapshot['reported_projects']} projects / "
        f"{snapshot['third_party_reported_gw']:.1f} reported GW**",
        "",
        "## The published record",
        "",
    ]

    if not any(data[key] for key in ("units", "permits", "events", "facilities")):
        lines.extend(["No published record changes in this window.", ""])

    if data["units"]:
        lines.extend(["### Equipment", ""])
        for row in data["units"]:
            facility = _facility(row)
            label = _unit_label(row)
            total = _mw(row)
            lines.append(
                f"- [{facility.get('name') or facility.get('slug') or 'Facility'}]"
                f"({_dossier_url(row)}): {label}; {_number(total)} MW total; "
                f"basis `{row.get('basis') or 'not stated'}`; verification "
                f"`{row.get('verification_state') or 'not stated'}`. "
                f"{_source_suffix(row)}".rstrip()
            )
        lines.append("")

    if data["permits"]:
        lines.extend(["### Permits", ""])
        for row in data["permits"]:
            facility = _facility(row)
            dates = ", ".join(
                f"{label} {row[field]}" for label, field in (
                    ("filed", "filed_at"), ("issued", "issued_at")
                ) if row.get(field)
            ) or "dates not stated"
            lines.append(
                f"- [{facility.get('name') or facility.get('slug') or 'Facility'}]"
                f"({_dossier_url(row)}): {row['authority']} {row['permit_no']} — "
                f"{row['status']} ({dates}); verification "
                f"`{row.get('verification_state') or 'not stated'}`. "
                f"{_source_suffix(row)}".rstrip()
            )
        lines.append("")

    if data["events"]:
        lines.extend(["### Timeline", ""])
        for row in data["events"]:
            facility = _facility(row)
            lines.append(
                f"- **{row['event_date']}** — "
                f"[{facility.get('name') or facility.get('slug') or 'Record'}]"
                f"({_dossier_url(row)}): {row['headline']}. "
                f"{_source_suffix(row)}".rstrip()
            )
        lines.append("")

    if data["facilities"]:
        lines.extend(["### Facility records", ""])
        for row in data["facilities"]:
            lines.append(
                f"- [{row['name']}]({_dossier_url(row)}): {row['state']}; "
                f"status `{row['status']}`; record updated {row['updated_at'][:10]}. "
                f"{_source_suffix(row)}".rstrip()
            )
        lines.append("")

    lines.extend([
        "## Third-party announced pipeline",
        "",
        "These rows are reported by a cited third-party inventory. They are not "
        "BTW-verified operating capacity and are never added to the verified total.",
        "",
    ])
    if not data["announcements"]:
        lines.extend(["No published third-party inventory updates in this window.", ""])
    else:
        for row in data["announcements"]:
            document = row.get("source_document") or {}
            source = _valid_url(document.get("url"))
            source_text = f" [source]({source})" if source else ""
            capacity = (
                f"{_number(float(row['reported_capacity_mw']))} MW reported"
                if row.get("reported_capacity_mw") is not None
                else "capacity not stated"
            )
            location = ", ".join(
                value for value in (row.get("county"), row.get("state")) if value
            ) or "location not stated"
            lines.append(
                f"- **{row['name']}** — {location}; "
                f"{row.get('operating_status') or 'status not stated'}; "
                f"{capacity}.{source_text}"
            )
        lines.append("")

    lines.extend([
        "## Method",
        "",
        "The verified section is generated from published facts and sealed review "
        "manifests. Staging facts and watcher candidates are excluded. The "
        "third-party pipeline is kept separate and explicitly labelled.",
        "",
        f"Explore the data: {SITE_ORIGIN}/data/",
        "",
        "All data CC BY 4.0. Cite as: Behind the Watt, behindthewatt.com.",
        "",
    ])
    return "\n".join(lines)


def _html_list(items: list[str]) -> str:
    return '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin:14px 0 24px;border-collapse:separate;border-spacing:0 10px">' + "".join(
        '<tr><td style="padding:16px 18px;border:1px solid #e6e4dc;'
        'border-radius:9px;background:#ffffff;color:#38372f;font-size:14px;'
        f'line-height:1.6">{item}</td></tr>'
        for item in items
    ) + "</table>"


def _html_link(label: Any, url: str) -> str:
    return (
        f'<a href="{html.escape(url, quote=True)}" '
        'style="color:#157a54;text-decoration:underline;text-decoration-color:#9bcbb4">'
        f"{html.escape(str(label))}</a>"
    )


def _html_sources(row: dict[str, Any]) -> str:
    urls = row.get("sources") or []
    if not urls:
        direct = _valid_url(row.get("source_url"))
        urls = [direct] if direct else []
    return " ".join(
        _html_link(f"source {index + 1}", url)
        for index, url in enumerate(urls)
    )


def render_html(issue_id: str, start: dt.datetime, end: dt.datetime,
                data: dict[str, Any]) -> str:
    snapshot = data["snapshot"]
    sections: list[str] = []

    record_items: list[str] = []
    for row in data["units"]:
        facility = _facility(row)
        label = _unit_label(row)
        record_items.append(
            f"{_html_link(facility.get('name') or facility.get('slug') or 'Facility', _dossier_url(row))}: "
            f"{html.escape(label)}; {_number(_mw(row))} MW total; "
            f"{html.escape(row.get('basis') or 'basis not stated')}; "
            f"{html.escape(row.get('verification_state') or 'verification not stated')}. "
            f"{_html_sources(row)}"
        )
    for row in data["permits"]:
        facility = _facility(row)
        dates = ", ".join(
            f"{label} {row[field]}" for label, field in (
                ("filed", "filed_at"), ("issued", "issued_at")
            ) if row.get(field)
        ) or "dates not stated"
        record_items.append(
            f"{_html_link(facility.get('name') or facility.get('slug') or 'Facility', _dossier_url(row))}: "
            f"{html.escape(row['authority'])} {html.escape(row['permit_no'])} — "
            f"{html.escape(row['status'])} ({html.escape(dates)}). {_html_sources(row)}"
        )
    for row in data["events"]:
        facility = _facility(row)
        record_items.append(
            f"<strong>{html.escape(str(row['event_date']))}</strong> — "
            f"{_html_link(facility.get('name') or facility.get('slug') or 'Record', _dossier_url(row))}: "
            f"{html.escape(row['headline'])}. {_html_sources(row)}"
        )
    for row in data["facilities"]:
        record_items.append(
            f"{_html_link(row['name'], _dossier_url(row))}: "
            f"{html.escape(row['state'])}; status {html.escape(row['status'])}; "
            f"record updated {html.escape(row['updated_at'][:10])}. {_html_sources(row)}"
        )
    sections.append(
        _html_list(record_items) if record_items
        else '<div style="margin:14px 0 26px;padding:18px;border:1px solid #e6e4dc;border-radius:9px;background:#fff;color:#57564e;line-height:1.6">No published record changes in this window.</div>'
    )

    announced_items: list[str] = []
    for row in data["announcements"]:
        document = row.get("source_document") or {}
        source = _valid_url(document.get("url"))
        location = ", ".join(
            value for value in (row.get("county"), row.get("state")) if value
        ) or "location not stated"
        capacity = (
            f"{_number(float(row['reported_capacity_mw']))} MW reported"
            if row.get("reported_capacity_mw") is not None
            else "capacity not stated"
        )
        source_html = f" {_html_link('source', source)}" if source else ""
        announced_items.append(
            f"<strong>{html.escape(row['name'])}</strong> — "
            f"{html.escape(location)}; "
            f"{html.escape(row.get('operating_status') or 'status not stated')}; "
            f"{html.escape(capacity)}.{source_html}"
        )
    sections.append(
        _html_list(announced_items) if announced_items
        else '<div style="margin:14px 0 8px;padding:18px;border:1px solid #ead9b3;border-radius:9px;background:#fffaf0;color:#6b5730;line-height:1.6">No published third-party inventory updates in this window.</div>'
    )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body style="margin:0;background:#fbfaf7;color:#14140f;font-family:Arial,sans-serif">
<div style="display:none;max-height:0;overflow:hidden">{html.escape(_preview(data))}</div>
<div style="max-width:680px;margin:0 auto;padding:28px 16px 44px">
  <div style="background:#ffffff;border:1px solid #e6e4dc;border-radius:12px;overflow:hidden">
    <div style="height:5px;background:#157a54;font-size:0;line-height:0">&nbsp;</div>
    <div style="padding:27px 34px 30px;border-bottom:1px solid #e6e4dc">
      <table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr>
        <td style="font-family:Georgia,serif;font-size:21px;color:#14140f">behind <span style="color:#157a54">|</span> the watt</td>
        <td align="right" style="font-family:monospace;font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:#8a897f">BTW Weekly</td>
      </tr></table>
      <p style="margin:34px 0 10px;font-family:monospace;font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:#157a54">Published record · {html.escape(issue_id)}</p>
      <h1 style="margin:0;max-width:560px;font-family:Georgia,serif;font-size:40px;line-height:1.08;font-weight:500;letter-spacing:-.02em">What changed behind the watt</h1>
      <p style="margin:16px 0 0;color:#8a897f;font-size:12px;line-height:1.5">Coverage: {html.escape(_iso(start))} to {html.escape(_iso(end))}</p>
    </div>
    <div style="padding:30px 34px 34px">
      <p style="margin:0 0 28px;font-family:Georgia,serif;font-size:22px;line-height:1.45;color:#2a2923">{html.escape(_meaning(data))}</p>
      <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin:0 0 34px;border-collapse:collapse">
        <tr>
          <td width="34%" valign="top" style="padding:17px 15px;border:1px solid #b9dcca;background:#e4f3ec"><div style="font-family:Georgia,serif;font-size:26px;color:#0f5f40">{snapshot['operating_gw']:.2f} GW</div><div style="margin-top:5px;color:#315b49;font-size:10px;font-weight:700;letter-spacing:.07em;text-transform:uppercase">Verified operating</div></td>
          <td width="27%" valign="top" style="padding:17px 15px;border:1px solid #e6e4dc;background:#fff"><div style="font-family:Georgia,serif;font-size:26px;color:#14140f">{snapshot['published_facilities']}</div><div style="margin-top:5px;color:#8a897f;font-size:10px;font-weight:700;letter-spacing:.07em;text-transform:uppercase">Facilities</div></td>
          <td width="39%" valign="top" style="padding:17px 15px;border:1px solid #ead9b3;background:#f8efdb"><div style="font-family:Georgia,serif;font-size:26px;color:#76520c">{snapshot['third_party_reported_gw']:.1f} GW</div><div style="margin-top:5px;color:#76520c;font-size:10px;font-weight:700;letter-spacing:.07em;text-transform:uppercase">Third-party reported</div></td>
        </tr>
      </table>
      <p style="margin:0 0 7px;font-family:monospace;font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:#157a54">Verified layer</p>
      <h2 style="margin:0;font-family:Georgia,serif;font-size:28px;font-weight:500">The published record</h2>
      {sections[0]}
      <div style="margin:34px -10px 0;padding:24px 24px 16px;border:1px solid #ead9b3;border-radius:10px;background:#f8efdb">
        <p style="margin:0 0 7px;font-family:monospace;font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:#9a6b10">Reported layer</p>
        <h2 style="margin:0 0 9px;font-family:Georgia,serif;font-size:28px;font-weight:500;color:#3b311e">Third-party announced pipeline</h2>
        <p style="margin:0;color:#6b5730;font-size:13px;line-height:1.6">Reported by a cited third-party inventory. These rows are not BTW-verified operating capacity and are never added to the verified total.</p>
        {sections[1]}
      </div>
      <div style="margin:36px 0 0;padding:26px;border-radius:10px;background:#14140f;color:#fbfaf7">
        <p style="margin:0 0 8px;color:#8fc7aa;font-family:monospace;font-size:10px;letter-spacing:.12em;text-transform:uppercase">The evidence stays inspectable</p>
        <p style="margin:0 0 18px;font-family:Georgia,serif;font-size:25px;line-height:1.3">Every number should lead back to a record.</p>
        <a href="{SITE_ORIGIN}/data/" style="display:inline-block;padding:12px 18px;border-radius:7px;background:#e4f3ec;color:#0f5f40;text-decoration:none;font-weight:700">Inspect the data →</a>
      </div>
    </div>
    <div style="padding:23px 34px;background:#f4f2ec;border-top:1px solid #e6e4dc;color:#77756c;font-size:11px;line-height:1.65">
      <p style="margin:0 0 8px">Generated from published facts and sealed review manifests. Staging facts and watcher candidates are excluded.</p>
      <p style="margin:0 0 8px">All data CC BY 4.0. Cite as: Behind the Watt, behindthewatt.com.</p>
      <p style="margin:0"><a href="{UNSUBSCRIBE_URL}" style="color:#57564e">Unsubscribe</a></p>
    </div>
  </div>
</div></body></html>"""


def _preview(data: dict[str, Any]) -> str:
    verified = sum(len(data[key]) for key in (
        "units", "permits", "events", "facilities"
    ))
    return (
        f"{verified} published record update{'s' if verified != 1 else ''}; "
        f"verified operating record {data['snapshot']['operating_gw']:.2f} GW."
    )


def render_text(issue_id: str, start: dt.datetime, end: dt.datetime,
                data: dict[str, Any]) -> str:
    markdown = render_markdown(issue_id, start, end, data)
    return (
        markdown
        + "\n\nUnsubscribe: " + UNSUBSCRIBE_URL
        + "\n"
    )


def content_hash(subject: str, html_body: str, text_body: str) -> str:
    payload = f"{subject}\0{html_body}\0{text_body}".encode()
    return hashlib.sha256(payload).hexdigest()


def build_edition(issue_id: str, start: dt.datetime, end: dt.datetime,
                  data: dict[str, Any]) -> dict[str, Any]:
    subject = _subject(issue_id, data)
    html_body = render_html(issue_id, start, end, data)
    text_body = render_text(issue_id, start, end, data)
    return {
        "mode": "review",
        "issue_id": issue_id,
        "window_start": _iso(start),
        "window_end": _iso(end),
        "subject": subject,
        "preview_text": _preview(data),
        "html": html_body,
        "text": text_body,
        "content_sha256": content_hash(subject, html_body, text_body),
    }


def deliver(edition: dict[str, Any], endpoint: str = DEFAULT_ENDPOINT) -> dict[str, Any]:
    secret = os.environ.get("DIGEST_TRIGGER_SECRET")
    if not secret:
        raise RuntimeError(
            "DIGEST_TRIGGER_SECRET is required for --request-review"
        )
    response = httpx.post(
        endpoint,
        headers={"Authorization": f"Bearer {secret}"},
        json=edition,
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def validate_delivery(endpoint: str = DEFAULT_ENDPOINT) -> dict[str, Any]:
    secret = os.environ.get("DIGEST_TRIGGER_SECRET")
    if not secret:
        raise RuntimeError("DIGEST_TRIGGER_SECRET is required for validation")
    response = httpx.post(
        endpoint,
        headers={"Authorization": f"Bearer {secret}"},
        json={"mode": "validate"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def delivery_cursor(endpoint: str = DEFAULT_ENDPOINT) -> dt.datetime | None:
    """Return the end of the latest editor-approved delivery window."""
    secret = os.environ.get("DIGEST_TRIGGER_SECRET")
    if not secret:
        raise RuntimeError("DIGEST_TRIGGER_SECRET is required for cursor lookup")
    response = httpx.post(
        endpoint,
        headers={"Authorization": f"Bearer {secret}"},
        json={"mode": "cursor"},
        timeout=30,
    )
    response.raise_for_status()
    value = response.json().get("window_end")
    return _datetime(value) if isinstance(value, str) else None


def previous_window_end(archive: Path, issue_id: str,
                        default: dt.datetime) -> dt.datetime:
    latest: dt.datetime | None = None
    if not archive.exists():
        return default
    for path in archive.glob("????-??-??.json"):
        if path.stem >= issue_id:
            continue
        try:
            raw = json.loads(path.read_text())
            value = dt.datetime.fromisoformat(raw["window"]["end"].replace("Z", "+00:00"))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if latest is None or value > latest:
            latest = value
    return latest or default


def review_window_end(issue_date: dt.date) -> dt.datetime:
    """Freeze a Tuesday edition for review at 13:00 UTC on Monday."""
    return dt.datetime.combine(
        issue_date - dt.timedelta(days=1),
        dt.time(hour=13),
        tzinfo=dt.timezone.utc,
    )


def write_receipt(archive: Path, issue_id: str, start: dt.datetime,
                  end: dt.datetime, data: dict[str, Any],
                  edition: dict[str, Any], delivery: dict[str, Any]) -> None:
    archive.mkdir(parents=True, exist_ok=True)
    (archive / f"{issue_id}.md").write_text(
        render_markdown(issue_id, start, end, data) + "\n"
    )
    (archive / f"{issue_id}.html").write_text(edition["html"])
    receipt = {
        "issue_id": issue_id,
        "window": {"start": _iso(start), "end": _iso(end)},
        "subject": edition["subject"],
        "preview_text": edition["preview_text"],
        "content_sha256": edition["content_sha256"],
        "counts": {
            key: len(data[key]) for key in (
                "reviews", "units", "permits", "events", "facilities",
                "announcements",
            )
        },
        "snapshot": data["snapshot"],
        "review_manifests": data["reviews"],
        "delivery": delivery,
    }
    (archive / f"{issue_id}.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True, default=str) + "\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--issue-date", default=dt.date.today().isoformat())
    parser.add_argument("--archive", default="digest")
    parser.add_argument("--endpoint", default=os.environ.get(
        "NEWSLETTER_ENDPOINT", DEFAULT_ENDPOINT))
    parser.add_argument("--request-review", action="store_true")
    parser.add_argument("--validate-delivery", action="store_true")
    args = parser.parse_args()

    if args.validate_delivery:
        print(json.dumps(validate_delivery(args.endpoint), sort_keys=True))
        return
    if not ISSUE_RE.fullmatch(args.issue_date):
        parser.error("--issue-date must be YYYY-MM-DD")

    issue_date = dt.date.fromisoformat(args.issue_date)
    # The Tuesday edition is assembled on Monday. The half-open data window
    # ends when review begins, so facts cannot appear after the editor preview.
    end = review_window_end(issue_date)
    archive = Path(args.archive)
    default_start = end - dt.timedelta(days=7)
    if args.request_review:
        start = delivery_cursor(args.endpoint) or default_start
    else:
        start = previous_window_end(archive, args.issue_date, default_start)
    if start >= end:
        raise RuntimeError("edition window start must be before end")

    data = gather(start, end)
    edition = build_edition(args.issue_date, start, end, data)
    delivery = (
        deliver(edition, args.endpoint)
        if args.request_review else {"status": "dry_run", "broadcast_id": None}
    )
    write_receipt(archive, args.issue_date, start, end, data, edition, delivery)
    print(json.dumps({
        "issue_id": args.issue_date,
        "window": {"start": _iso(start), "end": _iso(end)},
        "subject": edition["subject"],
        "content_sha256": edition["content_sha256"],
        "delivery": delivery,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
