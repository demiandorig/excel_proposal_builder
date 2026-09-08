"""
Net/Gross proposal — PowerPoint export (Step 07).

A deliberately simple, signature-ready deck — NOT a full recreation of the
Excel proposal. One slide (header + media plan table + signature block) for
a typical proposal; a longer one paginates the table across as many slides
as it needs, with the totals + signature block landing on the last one —
never crammed onto a single overflowing slide, never padded out to extra
slides it doesn't need. Table stops at the Net/Gross Budget column, same
"not a contract detail" boundary the Excel print-area change uses — no
avails/notes here either. Built with manual shapes on a blank layout
throughout (not the default theme's placeholders) so every color/font is
exactly the Entravision spec below, not whatever python-pptx's built-in
theme would otherwise apply.

Brand spec (as given):
  Background: white
  Logo: Entravision wordmark (local asset — see _LOGO_PATH)
  Main font: Poppins Bold | Secondary: Open Sans Regular | Headings: Open Sans Thin
  Palette: Primary #910e95, Secondary #ff003c, Tertiary #c800b4,
           Quaternary #0098e2, Quinary #ff6c02

Font rendering caveat (same as docx_builder.py's own): python-pptx can only
set a font NAME into the file — it can't embed the actual font. Poppins/
Open Sans render correctly on a machine that has them installed (or when
opened in PowerPoint/Google Slides' web font fallback); otherwise the
viewer substitutes its own default. "Open Sans Thin" specifically has no
native bold/weight toggle in pptx the way bold/italic do — this sets that
literal family name, which only renders as a true thin weight if that
exact static family is installed on the viewer's machine.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

try:
    from pptx import Presentation
    from pptx.util import Inches, Pt, Emu
    from pptx.dml.color import RGBColor
    from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
    from pptx.enum.shapes import MSO_SHAPE
    _HAS_PPTX = True
except ImportError:
    _HAS_PPTX = False

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
# The user's given URL (EVC_Claim_Purple.png) 404'd when fetched — this is
# the closest already-vetted, real Entravision brand asset in the repo (the
# purple gradient "e" mark used elsewhere in this app) used as a stand-in.
# Swap this file for the real wordmark lockup the moment it's available;
# nothing else about the deck needs to change.
_LOGO_PATH = _STATIC_DIR / "entravision-logo.png"

# ---------------------------------------------------------------------------
# Brand constants
# ---------------------------------------------------------------------------

if _HAS_PPTX:
    WHITE = RGBColor(0xFF, 0xFF, 0xFF)
    INK = RGBColor(0x22, 0x1A, 0x24)
    INK_SOFT = RGBColor(0x5A, 0x52, 0x5C)
    PRIMARY = RGBColor(0x91, 0x0E, 0x95)
    SECONDARY = RGBColor(0xFF, 0x00, 0x3C)
    TERTIARY = RGBColor(0xC8, 0x00, 0xB4)
    QUATERNARY = RGBColor(0x00, 0x98, 0xE2)
    QUINARY = RGBColor(0xFF, 0x6C, 0x02)
    ROW_ALT_FILL = RGBColor(0xF7, 0xEF, 0xF7)  # a very light tint of PRIMARY, for zebra striping
    RULE = RGBColor(0xE0, 0xD8, 0xE0)

    FONT_MAIN = "Poppins"        # bold headline/emphasis text
    FONT_BODY = "Open Sans"      # regular body/table text
    FONT_HEADING = "Open Sans Thin"  # section headings

    SLIDE_W = Inches(13.333)
    SLIDE_H = Inches(7.5)
    MARGIN = Inches(0.5)
    CONTENT_W = SLIDE_W - 2 * MARGIN
    FOOTER_Y = SLIDE_H - Inches(0.4)

    # --- Table column widths (in inches, sum must equal CONTENT_W's 12.333") ---
    # The actual bug a real export surfaced: these used to sum to LESS than
    # CONTENT_W, silently starving whatever column absorbed the remainder
    # (Total ended up ~0.53" wide — barely enough for "Tota", let alone a
    # real dollar figure, hence both the wrapped header and wrapped values
    # in the reported screenshot). Verify this list sums to 12.333 after any
    # future edit — nothing auto-corrects a mismatch here.
    _COL_WIDTHS_IN = [3.6, 3.6, 0.9, 2.1, 2.133]  # Product, Target, Months, Monthly Budget, Total
    assert abs(sum(_COL_WIDTHS_IN) - 12.333) < 0.01, "pptx table column widths must sum to CONTENT_W"

    # Y where content starts: slide 1 has the full header block below it;
    # slide 2+ only has a small logo, so content can start much higher.
    HEADER_CONTENT_TOP_IN = 2.45
    NO_HEADER_CONTENT_TOP_IN = 0.85
    FOOTER_Y_IN = FOOTER_Y / 914400  # EMU -> inches, so this stays a plain float everywhere else

    # Table row heights are computed per-row from real content (see
    # _row_height_in) — NOT a flat per-row guess. A first attempt at "how
    # many rows fit on one slide" used a fixed row count, and a real export
    # (short text, exactly at that count) still overflowed the signature
    # block past the footer, and a longer-text version overflowed the slide
    # entirely — six rows of TEXT is not a fixed height, it depends on how
    # much of it wraps. Pagination below measures the actual height each
    # candidate chunk needs against the actual space available and only
    # adds a row while it still fits — see _greedy_chunk / build_signature_deck.


def _set_background_white(slide) -> None:
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = WHITE


def _add_textbox(slide, left, top, width, height, text, *, font=None,
                  size=12, bold=False, color=None, align=None,
                  anchor=None, wrap=True):
    font = font or FONT_BODY
    color = color if color is not None else INK
    align = align if align is not None else PP_ALIGN.LEFT
    anchor = anchor if anchor is not None else MSO_ANCHOR.TOP
    box = slide.shapes.add_textbox(left, top, width, height)
    tf = box.text_frame
    tf.word_wrap = wrap
    tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    p = tf.paragraphs[0]
    p.alignment = align
    run = p.add_run()
    run.text = text
    run.font.name = font
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color
    return box


def _add_rule(slide, left, top, width, *, height=None, color=None):
    height = height if height is not None else Pt(3)
    color = color if color is not None else PRIMARY
    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, left, top, width, height)
    shape.fill.solid()
    shape.fill.fore_color.rgb = color
    shape.line.fill.background()
    return shape


def _add_logo(slide, *, left=None, top=None, height=None):
    left = left if left is not None else MARGIN
    top = top if top is not None else Inches(0.35)
    height = height if height is not None else Inches(0.4)
    if _LOGO_PATH.exists():
        try:
            slide.shapes.add_picture(str(_LOGO_PATH), left, top, height=height)
            return
        except Exception:
            pass
    # Graceful text fallback if the asset is ever missing — never let a
    # missing logo file break the whole export.
    _add_textbox(slide, left, top, Inches(3), height, "ENTRAVISION",
                 font=FONT_MAIN, size=16, bold=True, color=PRIMARY)


def _footer(slide, page_label: str):
    _add_textbox(slide, MARGIN, FOOTER_Y, Inches(8), Inches(0.3),
                 "Entravision Communications Corporation", font=FONT_BODY, size=8, color=INK_SOFT)
    if page_label:
        _add_textbox(slide, SLIDE_W - MARGIN - Inches(2), FOOTER_Y, Inches(2), Inches(0.3),
                     page_label, font=FONT_BODY, size=8, color=INK_SOFT, align=PP_ALIGN.RIGHT)


def _add_palette_strip(slide):
    """A light brand touch along the very bottom edge — not a data element."""
    strip_colors = [PRIMARY, TERTIARY, SECONDARY, QUATERNARY, QUINARY]
    strip_w = SLIDE_W / len(strip_colors)
    for i, c in enumerate(strip_colors):
        _add_rule(slide, Emu(int(strip_w * i)), SLIDE_H - Inches(0.1), Emu(int(strip_w)) + Emu(1),
                  height=Inches(0.1), color=c)


# ---------------------------------------------------------------------------
# Header block — cover info, shown only on the first slide
# ---------------------------------------------------------------------------

def _add_header(slide, *, proposal_title: str, client_name: str, requested_by: str,
                start_date: str, end_date: str, gross: bool) -> float:
    """Returns the Y position (inches, as a float) where content below the
    header should start."""
    _add_logo(slide)

    kicker = "GROSS MEDIA PROPOSAL" if gross else "NET MEDIA PROPOSAL"
    _add_textbox(slide, MARGIN, Inches(0.85), Inches(10), Inches(0.3), kicker,
                 font=FONT_HEADING, size=13, color=SECONDARY)
    _add_textbox(slide, MARGIN, Inches(1.15), CONTENT_W, Inches(0.45),
                 client_name or "Client", font=FONT_MAIN, size=24, bold=True, color=PRIMARY)

    flight = f"  ·  {start_date} – {end_date}" if (start_date and end_date) else ""
    subtitle = f"Prepared by {requested_by or 'Entravision'}{flight}"
    _add_textbox(slide, MARGIN, Inches(1.62), CONTENT_W, Inches(0.3),
                 subtitle, font=FONT_BODY, size=10.5, color=INK_SOFT)
    _add_textbox(slide, MARGIN, Inches(1.92), CONTENT_W, Inches(0.25),
                 proposal_title, font=FONT_BODY, size=9, color=INK_SOFT)

    _add_rule(slide, MARGIN, Inches(2.25), Inches(1.6), color=QUINARY)
    return 2.45


# ---------------------------------------------------------------------------
# Table + signature — the content every slide actually carries
# ---------------------------------------------------------------------------

def _estimate_wrapped_lines(text: str, *, chars_per_line: int = 38, max_lines: int = 3) -> int:
    """How many lines `text` will wrap to in a ~3.6"-wide column at 10-11pt
    Open Sans. chars_per_line is a deliberately conservative estimate
    (rounded down from a rough 0.085"/char average) — better to slightly
    over-book a row's height than under-book it and have PowerPoint's own
    auto-fit silently grow a row taller than this file calculated, which is
    exactly what caused the totals-row/signature overlap a real export
    surfaced: row height was assumed flat/fixed instead of measured against
    what the row's own longest cell actually needs."""
    if not text:
        return 1
    return max(1, min(-(-len(text) // chars_per_line), max_lines))


def _row_height_in(product_name: str, target_text: str, *, header: bool = False) -> float:
    if header:
        return 0.34
    lines = max(_estimate_wrapped_lines(product_name), _estimate_wrapped_lines(target_text))
    return 0.19 * lines + 0.1  # per-line height + fixed cell padding (top+bottom margins)


def _add_table(slide, *, top_in: float, chunk, gross: bool, agency_fee: Optional[float],
              show_header_row: bool, running_total: float) -> tuple[float, float]:
    """Draws the product rows for this slide's chunk (+ a header row when
    show_header_row). Returns (new_running_total, y_in_inches_after_table) —
    y_after is the exact sum of the row heights this function itself just
    set, not a separate estimate that could drift from what's actually on
    the slide."""
    columns = ["Product", "Target", "Months", "Monthly Budget", "Total"]
    n_rows = len(chunk) + (1 if show_header_row else 0)
    left, top = MARGIN, Inches(top_in)
    row_heights_in = [_row_height_in("", "", header=True)] if show_header_row else []
    row_heights_in += [_row_height_in(p.name, li.target_override or "") for p, li in chunk]
    gfx = slide.shapes.add_table(n_rows, len(columns), left, top, CONTENT_W, Inches(sum(row_heights_in)))
    table = gfx.table
    for i, w in enumerate(_COL_WIDTHS_IN):
        table.columns[i].width = Inches(w)
    for i, h in enumerate(row_heights_in):
        table.rows[i].height = Inches(h)

    def _cell(r, c, text, *, bold=False, color=None, align=None, size=11,
              font=None, fill=None):
        color = color if color is not None else INK
        align = align if align is not None else PP_ALIGN.LEFT
        font = font or FONT_BODY
        cell = table.cell(r, c)
        cell.margin_left = cell.margin_right = Pt(6)
        cell.margin_top = cell.margin_bottom = Pt(3)
        cell.vertical_anchor = MSO_ANCHOR.MIDDLE
        cell.fill.solid()
        cell.fill.fore_color.rgb = fill if fill is not None else WHITE
        tf = cell.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.alignment = align
        run = p.add_run()
        run.text = text
        run.font.name = font
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.color.rgb = color

    row_offset = 0
    if show_header_row:
        for c, label in enumerate(columns):
            align = PP_ALIGN.LEFT if c < 2 else (PP_ALIGN.CENTER if c == 2 else PP_ALIGN.RIGHT)
            _cell(0, c, label, bold=True, color=WHITE, align=align, size=11, font=FONT_MAIN, fill=PRIMARY)
        row_offset = 1

    for i, (product, li) in enumerate(chunk):
        r = i + row_offset
        fill = ROW_ALT_FILL if i % 2 == 1 else WHITE
        target = li.target_override or "—"
        monthly = li.monthly_budget
        line_total = monthly * li.months
        if gross and agency_fee:
            monthly = monthly / (1 - agency_fee)
            line_total = line_total / (1 - agency_fee)
        running_total += line_total
        _cell(r, 0, product.name, fill=fill)
        _cell(r, 1, target, color=INK_SOFT, size=10, fill=fill)
        _cell(r, 2, str(li.months), align=PP_ALIGN.CENTER, fill=fill)
        _cell(r, 3, "Added Value" if li.is_added_value else f"${monthly:,.0f}/mo", align=PP_ALIGN.RIGHT, fill=fill)
        _cell(r, 4, f"${line_total:,.0f}", align=PP_ALIGN.RIGHT, bold=True, fill=fill)

    y_after = top_in + sum(row_heights_in) + 0.15
    return running_total, y_after


def _add_totals_row(slide, *, top_in: float, total: float) -> float:
    row_h = _row_height_in("", "", header=True)  # same fixed height as a header row — always 1 line
    left, top = MARGIN, Inches(top_in)
    gfx = slide.shapes.add_table(1, 5, left, top, CONTENT_W, Inches(row_h))
    table = gfx.table
    for i, w in enumerate(_COL_WIDTHS_IN):
        table.columns[i].width = Inches(w)
    table.rows[0].height = Inches(row_h)
    for c in range(5):
        cell = table.cell(0, c)
        cell.margin_left = cell.margin_right = Pt(6)
        cell.margin_top = cell.margin_bottom = Pt(3)
        cell.vertical_anchor = MSO_ANCHOR.MIDDLE
        cell.fill.solid()
        cell.fill.fore_color.rgb = TERTIARY
        tf = cell.text_frame
        p = tf.paragraphs[0]
        if c == 0:
            p.text = "TOTAL"
        elif c == 4:
            p.text = f"${total:,.0f}"
            p.alignment = PP_ALIGN.RIGHT
        run = p.runs[0] if p.runs else p.add_run()
        run.font.name = FONT_MAIN
        run.font.size = Pt(12)
        run.font.bold = True
        run.font.color.rgb = WHITE
    return top_in + row_h + 0.2


def _add_signature_block(slide, *, client_name: str, top_in: float):
    terms = (
        "Valid for 1 month after presentation — not a guarantee of delivery. All rates are NET "
        f"unless marked otherwise. By signing below, {client_name or 'the client'} authorizes "
        "Entravision Communications Corporation to proceed with this media plan."
    )
    _add_textbox(slide, MARGIN, Inches(top_in), CONTENT_W, Inches(0.55), terms,
                 font=FONT_BODY, size=9.5, color=INK)

    sig_top = top_in + 0.75
    line_w = Inches(4.6)
    _add_rule(slide, MARGIN, Inches(sig_top), line_w, height=Pt(1), color=INK_SOFT)
    _add_textbox(slide, MARGIN, Inches(sig_top + 0.08), line_w, Inches(0.3),
                 "Client Signature", font=FONT_BODY, size=9, color=INK_SOFT)
    _add_rule(slide, Inches(7.2), Inches(sig_top), Inches(2.0), height=Pt(1), color=INK_SOFT)
    _add_textbox(slide, Inches(7.2), Inches(sig_top + 0.08), Inches(2.0), Inches(0.3),
                 "Date", font=FONT_BODY, size=9, color=INK_SOFT)

    sig_top2 = sig_top + 0.75
    _add_rule(slide, MARGIN, Inches(sig_top2), line_w, height=Pt(1), color=INK_SOFT)
    _add_textbox(slide, MARGIN, Inches(sig_top2 + 0.08), line_w, Inches(0.3),
                 "Print Name / Title", font=FONT_BODY, size=9, color=INK_SOFT)


if _HAS_PPTX:
    # Must match _add_signature_block's own internal geometry exactly (two
    # 0.75" hops down from its top_in, plus the final label's own offset +
    # height) — kept as one named constant instead of copy-pasted math so
    # the two can't silently drift apart the way the original flat-row-
    # count pagination drifted from what these blocks actually render at.
    _SIGNATURE_HEIGHT_IN = 0.75 + 0.75 + 0.08 + 0.3
    _TOTALS_ROW_HEIGHT_IN = 0.34 + 0.2  # _row_height_in(header=True) + the gap _add_totals_row itself adds


def _greedy_chunk(pairs: list, *, start_idx: int, avail_height_in: float, reserve_after_in: float):
    """Fill as many (product, line_item) pairs, starting at start_idx, as
    fit within avail_height_in once reserve_after_in (whatever needs to
    follow the table on THIS slide — totals+signature, or nothing) is set
    aside. Always takes at least one row even if it alone doesn't fit,
    so a single pathological row can't spin this into an infinite loop —
    that row will just render however tall it needs to.
    Returns (chunk, next_idx)."""
    used = _row_height_in("", "", header=True)  # the table's own header row, always present
    chunk = []
    idx = start_idx
    while idx < len(pairs):
        p, li = pairs[idx]
        h = _row_height_in(p.name, li.target_override or "")
        if chunk and used + h + reserve_after_in > avail_height_in:
            break
        chunk.append((p, li))
        used += h
        idx += 1
    return chunk, idx


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def build_signature_deck(
    request, products: list, line_items: list, output_path: Path, *,
    gross: bool, proposal_title: str = "",
) -> bool:
    """
    Build the simple, signature-ready Net or Gross deck — one slide when the
    product list is short enough (the common case), paginating the table
    across as many slides as it actually needs otherwise. Returns True on
    success, False if python-pptx isn't installed (graceful skip, same
    pattern as docx_builder.py — never raises to the caller for a missing
    optional dependency).
    """
    if not _HAS_PPTX:
        return False
    if not products or not line_items:
        return False

    prs = Presentation()
    prs.slide_width = SLIDE_W
    prs.slide_height = SLIDE_H

    client_name = getattr(request, "client_name", "") or ""
    requested_by = getattr(request, "requested_by", "") or ""
    start_date = getattr(request, "start_date", "") or ""
    end_date = getattr(request, "end_date", "") or ""
    agency_fee = getattr(request, "agency_fee", None)
    fee_for_gross = agency_fee if gross else None

    pairs = list(zip(products, line_items))

    # Phase 1 — decide what goes on each slide, measured against the REAL
    # height each candidate chunk needs (see _greedy_chunk/_row_height_in),
    # not a flat row-count guess. Two attempts per slide: first try fitting
    # everything remaining WITH the totals+signature block reserved — if
    # that consumes every remaining row, this is the last slide, done.
    # Otherwise this slide doesn't get the signature block (there's more
    # table still to come), so redo the same slide's chunk WITHOUT
    # reserving that space, packing more rows into it before moving on.
    slide_plans: list[dict] = []
    idx = 0
    while idx < len(pairs):
        is_first = len(slide_plans) == 0
        content_top = HEADER_CONTENT_TOP_IN if is_first else NO_HEADER_CONTENT_TOP_IN
        avail = FOOTER_Y_IN - content_top

        reserve = _TOTALS_ROW_HEIGHT_IN + _SIGNATURE_HEIGHT_IN
        chunk, next_idx = _greedy_chunk(pairs, start_idx=idx, avail_height_in=avail, reserve_after_in=reserve)
        if next_idx == len(pairs):
            slide_plans.append({"chunk": chunk, "is_first": is_first, "is_last": True, "content_top": content_top})
            idx = next_idx
        else:
            # Doesn't reach the end WITH the signature reserved — try
            # packing this (non-final) slide more generously without that
            # reserve. But if THAT generous packing would itself reach the
            # end, it must NOT be used as-is: that would leave the deck's
            # last rows on a slide with nowhere left to put totals/
            # signature, silently dropping both (the real bug a stress
            # test caught — a 25-row deck rendered with no totals row at
            # all). Falling back to the smaller, reserve-respecting chunk
            # here guarantees at least one row is held back for a genuine
            # final slide, which — being smaller — will fit WITH reserve.
            generous_chunk, generous_next = _greedy_chunk(pairs, start_idx=idx, avail_height_in=avail, reserve_after_in=0.0)
            if generous_next == len(pairs):
                slide_plans.append({"chunk": chunk, "is_first": is_first, "is_last": False, "content_top": content_top})
                idx = next_idx
            else:
                slide_plans.append({"chunk": generous_chunk, "is_first": is_first, "is_last": False, "content_top": content_top})
                idx = generous_next

    total_slides = len(slide_plans)

    # Phase 2 — actually draw each slide from the plan above.
    running_total = 0.0
    for i, plan in enumerate(slide_plans):
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        _set_background_white(slide)

        if plan["is_first"]:
            _add_header(
                slide, proposal_title=proposal_title, client_name=client_name,
                requested_by=requested_by, start_date=start_date, end_date=end_date, gross=gross,
            )
        else:
            _add_logo(slide, height=Inches(0.3))

        running_total, y_after = _add_table(
            slide, top_in=plan["content_top"], chunk=plan["chunk"], gross=gross, agency_fee=fee_for_gross,
            show_header_row=True, running_total=running_total,
        )

        if plan["is_last"]:
            y_after = _add_totals_row(slide, top_in=y_after, total=running_total)
            _add_signature_block(slide, client_name=client_name, top_in=y_after)

        page_label = f"Media Plan {i + 1}/{total_slides}" if total_slides > 1 else "Media Plan"
        _footer(slide, page_label)
        if total_slides == 1:
            _add_palette_strip(slide)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(output_path))
    return True
