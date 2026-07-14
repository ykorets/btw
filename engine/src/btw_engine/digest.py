"""Build and deliver the deterministic BTW Weekly edition.

The newsletter is a view of the published record, not a second research
pipeline.  It reads exact rows promoted through sealed review manifests,
plus separately labelled published facility and third-party announcement
updates.  Candidates and staging facts are intentionally excluded.

The Tuesday workflow sends the rendered edition to the newsletter Worker.
The Worker owns the Resend key and the exactly-once delivery state.  A JSON
and Markdown receipt are written after delivery so a missed workflow can
resume from the last successful window without gaps.

Env: SUPABASE_URL, SUPABASE_SERVICE_KEY; for --send also
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
    return "<ul style=\"margin:12px 0 24px;padding-left:22px\">" + "".join(
        f"<li style=\"margin:0 0 12px;line-height:1.55\">{item}</li>"
        for item in items
    ) + "</ul>"


def _html_link(label: Any, url: str) -> str:
    return (
        f'<a href="{html.escape(url, quote=True)}" '
        'style="color:#a64b2a;text-decoration:underline">'
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
        else '<p style="color:#66645c;line-height:1.6">No published record changes in this window.</p>'
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
        else '<p style="color:#66645c;line-height:1.6">No published third-party inventory updates in this window.</p>'
    )

    return f"""<!doctype html>
<html><body style="margin:0;background:#f4f1e9;color:#181812;font-family:Arial,sans-serif">
<div style="display:none;max-height:0;overflow:hidden">{html.escape(_preview(data))}</div>
<div style="max-width:680px;margin:0 auto;padding:32px 18px">
  <div style="background:#fbfaf7;border:1px solid #d8d2c5;border-radius:12px;overflow:hidden">
    <div style="padding:32px 34px 24px;border-bottom:1px solid #d8d2c5">
      <p style="margin:0 0 14px;font-family:monospace;font-size:12px;letter-spacing:.1em;text-transform:uppercase;color:#8a897f">Behind the Watt · BTW Weekly</p>
      <h1 style="margin:0;font-family:Georgia,serif;font-size:36px;line-height:1.08;font-weight:500">The record for {html.escape(issue_id)}</h1>
      <p style="margin:14px 0 0;color:#77756c;font-size:13px">{html.escape(_iso(start))} → {html.escape(_iso(end))}</p>
    </div>
    <div style="padding:28px 34px">
      <p style="margin:0 0 26px;font-family:Georgia,serif;font-size:21px;line-height:1.45">{html.escape(_meaning(data))}</p>
      <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin:0 0 30px;border-collapse:collapse">
        <tr>
          <td style="padding:16px;border:1px solid #d8d2c5"><div style="font-family:Georgia,serif;font-size:26px">{snapshot['operating_gw']:.2f} GW</div><div style="color:#77756c;font-size:12px">verified operating</div></td>
          <td style="padding:16px;border:1px solid #d8d2c5"><div style="font-family:Georgia,serif;font-size:26px">{snapshot['published_facilities']}</div><div style="color:#77756c;font-size:12px">published facilities</div></td>
          <td style="padding:16px;border:1px solid #d8d2c5"><div style="font-family:Georgia,serif;font-size:26px">{snapshot['third_party_reported_gw']:.1f} GW</div><div style="color:#77756c;font-size:12px">third-party reported</div></td>
        </tr>
      </table>
      <h2 style="margin:0 0 8px;font-family:Georgia,serif;font-size:25px;font-weight:500">The published record</h2>
      {sections[0]}
      <h2 style="margin:30px 0 8px;font-family:Georgia,serif;font-size:25px;font-weight:500">Third-party announced pipeline</h2>
      <p style="margin:0;color:#66645c;font-size:13px;line-height:1.55">Reported by a cited third-party inventory. These rows are not BTW-verified operating capacity and are never added to the verified total.</p>
      {sections[1]}
      <p style="margin:30px 0 0"><a href="{SITE_ORIGIN}/data/" style="display:inline-block;padding:12px 18px;border-radius:7px;background:#181812;color:#fff;text-decoration:none;font-weight:600">Inspect the data</a></p>
    </div>
    <div style="padding:22px 34px;background:#efede6;color:#77756c;font-size:12px;line-height:1.6">
      <p style="margin:0 0 8px">Generated from published facts and sealed review manifests. Staging facts and watcher candidates are excluded.</p>
      <p style="margin:0 0 8px">All data CC BY 4.0. Cite as: Behind the Watt, behindthewatt.com.</p>
      <p style="margin:0"><a href="{UNSUBSCRIBE_URL}" style="color:#77756c">Unsubscribe</a></p>
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
        "issue_id": issue_id,
        "subject": subject,
        "preview_text": _preview(data),
        "html": html_body,
        "text": text_body,
        "content_sha256": content_hash(subject, html_body, text_body),
    }


def deliver(edition: dict[str, Any], endpoint: str = DEFAULT_ENDPOINT) -> dict[str, Any]:
    secret = os.environ.get("DIGEST_TRIGGER_SECRET")
    if not secret:
        raise RuntimeError("DIGEST_TRIGGER_SECRET is required for --send")
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


def write_receipt(archive: Path, issue_id: str, start: dt.datetime,
                  end: dt.datetime, data: dict[str, Any],
                  edition: dict[str, Any], delivery: dict[str, Any]) -> None:
    archive.mkdir(parents=True, exist_ok=True)
    (archive / f"{issue_id}.md").write_text(
        render_markdown(issue_id, start, end, data) + "\n"
    )
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
    parser.add_argument("--send", action="store_true")
    parser.add_argument("--validate-delivery", action="store_true")
    args = parser.parse_args()

    if args.validate_delivery:
        print(json.dumps(validate_delivery(args.endpoint), sort_keys=True))
        return
    if not ISSUE_RE.fullmatch(args.issue_date):
        parser.error("--issue-date must be YYYY-MM-DD")

    issue_date = dt.date.fromisoformat(args.issue_date)
    end = dt.datetime.combine(
        issue_date, dt.time(hour=13), tzinfo=dt.timezone.utc
    )
    archive = Path(args.archive)
    current_receipt = archive / f"{args.issue_date}.json"
    if current_receipt.exists() and args.send:
        print(f"edition {args.issue_date} already archived — no send")
        return
    start = previous_window_end(archive, args.issue_date, end - dt.timedelta(days=7))
    if start >= end:
        raise RuntimeError("edition window start must be before end")

    data = gather(start, end)
    edition = build_edition(args.issue_date, start, end, data)
    delivery = (
        deliver(edition, args.endpoint)
        if args.send else {"status": "dry_run", "broadcast_id": None}
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
