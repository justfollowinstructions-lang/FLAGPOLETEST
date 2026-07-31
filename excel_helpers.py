"""
NSE Flag & Pole Scanner - Excel Formatting Primitives
=========================================================
Generic xlsxwriter helpers: build a shared format dictionary, write a
DataFrame to a sheet with headers/widths/sorting/conditional
formatting hooks, round floats before writing, and convert a 0-indexed
column number to an Excel column letter.

These are intentionally pattern-agnostic — report_flag_pole.py is the
only thing here that knows about Flag & Pole specifically. If/when
this scanner is combined with a companion pattern scanner later, that
scanner's report module can import these same helpers instead of
duplicating the Excel plumbing.
"""

from __future__ import annotations

import pandas as pd


def round_floats(df: pd.DataFrame, dp: int = 2) -> pd.DataFrame:
    """
    Round all float columns to `dp` decimal places before writing to
    Excel. This prevents yfinance's raw float64 values (e.g.
    1112.599975585938) from appearing in cells instead of 1112.60.
    Non-numeric columns are untouched.
    """
    if df is None or df.empty:
        return df
    out = df.copy()
    for col in out.select_dtypes(include="float64").columns:
        out[col] = out[col].round(dp)
    return out


# ─── Formats ────────────────────────────────────────────────────────────────

def build_formats(workbook) -> dict:
    return {
        "header": workbook.add_format({
            "bold": True, "bg_color": "#1F4E78", "font_color": "white",
            "border": 1, "valign": "vcenter", "text_wrap": True,
        }),
        "money": workbook.add_format({"num_format": "₹#,##0.00"}),
        "money0": workbook.add_format({"num_format": "₹#,##0"}),
        "pct1": workbook.add_format({"num_format": "0.0%"}),
        "pct2": workbook.add_format({"num_format": "0.00%"}),
        "num0": workbook.add_format({"num_format": "0"}),
        "num1": workbook.add_format({"num_format": "0.0"}),
        "num2": workbook.add_format({"num_format": "0.00"}),
        "int": workbook.add_format({"num_format": "0"}),
        "date": workbook.add_format({"num_format": "dd-mmm-yyyy"}),
        "bool": workbook.add_format({"align": "center"}),
        "default": workbook.add_format({}),
        "green_bg": workbook.add_format({"bg_color": "#C6EFCE", "font_color": "#006100"}),
        "yellow_bg": workbook.add_format({"bg_color": "#FFEB9C", "font_color": "#9C6500"}),
        "orange_bold": workbook.add_format({"bold": True, "font_color": "#E36C09"}),
        "blue_bold": workbook.add_format({"bold": True, "font_color": "#1F4E78"}),
        "red_text": workbook.add_format({"font_color": "#C00000"}),
        "green_bold": workbook.add_format({"bold": True, "font_color": "#006100"}),
        "stop_capped_row": workbook.add_format({
            "bg_color": "#FFE0E0",   # light red background — noticeable but not aggressive
            "font_color": "#C00000",
        }),
        "title": workbook.add_format({"bold": True, "font_size": 16}),
        "subtitle": workbook.add_format({"bold": True, "font_size": 12, "font_color": "#1F4E78"}),
        "wrap": workbook.add_format({"text_wrap": True, "valign": "top"}),
    }


# ─── Generic sheet writer ──────────────────────────────────────────────────

# Column names that get converted from string/ISO date to a real Excel
# date value if present on the sheet being written. Listed centrally
# here (rather than per-caller) so every report module gets correct
# date rendering for free — extend this list, don't fork write_sheet,
# when a new pattern scanner introduces new date-like column names.
DATE_LIKE_COLUMNS = (
    "scan_date", "entry_date", "exit_date",
    "pole_start_date", "pole_end_date",
    "flag_start_date", "flag_end_date", "breakout_date",
)


def write_sheet(
    writer, workbook, fmts, df: pd.DataFrame, column_spec: list,
    sheet_name: str,
    sort_cols: list[str] | None = None,
    sort_asc: list[bool] | None = None,
    extra_conditional=None,
    extra_summary: list[str] | None = None,
    tab_color: str | None = None,
) -> None:
    if df is None or df.empty:
        empty_df = pd.DataFrame(columns=[c[0] for c in column_spec])
        empty_df.to_excel(writer, sheet_name=sheet_name, index=False, startrow=0)
        ws = writer.sheets[sheet_name]
        if tab_color:
            ws.set_tab_color(tab_color)
        for i, (_, header, width, _) in enumerate(column_spec):
            ws.write(0, i, header, fmts["header"])
            ws.set_column(i, i, width)
        ws.write(2, 0, "No signals found for this category in today's scan.")
        return

    cols_present = [c for c in column_spec if c[0] in df.columns]
    db_cols = [c[0] for c in cols_present]
    out = df[db_cols].copy()

    if sort_cols:
        valid_sort = [c for c in sort_cols if c in out.columns]
        if valid_sort:
            asc = sort_asc[: len(valid_sort)] if sort_asc else [True] * len(valid_sort)
            out = out.sort_values(by=valid_sort, ascending=asc, na_position="last")

    for date_col in DATE_LIKE_COLUMNS:
        if date_col in out.columns:
            out[date_col] = pd.to_datetime(out[date_col], errors="coerce")

    out.to_excel(writer, sheet_name=sheet_name, index=False, startrow=1, header=False)
    ws = writer.sheets[sheet_name]
    if tab_color:
        ws.set_tab_color(tab_color)

    for i, (_, header, width, _) in enumerate(cols_present):
        ws.write(0, i, header, fmts["header"])
        ws.set_column(i, i, width)

    n_rows = len(out)
    for i, (db_col, _, _, fmt_key) in enumerate(cols_present):
        if fmt_key and fmt_key in fmts:
            ws.set_column(i, i, None, fmts[fmt_key])

    ws.freeze_panes(1, 1)
    if n_rows > 0:
        ws.autofilter(0, 0, n_rows, len(cols_present) - 1)

    if extra_conditional:
        extra_conditional(ws, fmts, cols_present, n_rows)

    if extra_summary:
        start_row = n_rows + 3
        for j, line in enumerate(extra_summary):
            ws.write(start_row + j, 0, line)


def xl_col(idx: int) -> str:
    """0-indexed column number -> Excel column letter."""
    letters = ""
    idx += 1
    while idx > 0:
        idx, rem = divmod(idx - 1, 26)
        letters = chr(65 + rem) + letters
    return letters
