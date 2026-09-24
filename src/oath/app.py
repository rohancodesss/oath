"""Textual front end. M0: a read-only device check screen."""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.widgets import DataTable, Footer, Header, Static

from oath import __version__
from oath.devices import android_devices, check_tools, ios_devices


class OathApp(App):
    TITLE = "OATH"
    SUB_TITLE = f"OATH Allows Total History-transfer v{__version__}"
    BINDINGS = [("r", "refresh", "Rescan"), ("q", "quit", "Quit")]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Tools", classes="section")
        yield DataTable(id="tools")
        yield Static("Connected phones", classes="section")
        yield DataTable(id="phones")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#tools", DataTable).add_columns("Tool", "Status", "Path")
        self.query_one("#phones", DataTable).add_columns("Platform", "Identifier")
        self.action_refresh()

    def action_refresh(self) -> None:
        tools = self.query_one("#tools", DataTable)
        tools.clear()
        for tool in check_tools():
            tools.add_row(tool.name, "found" if tool.found else "MISSING", tool.path or "-")

        phones = self.query_one("#phones", DataTable)
        phones.clear()
        for serial in android_devices():
            phones.add_row("Android", serial)
        for udid in ios_devices():
            phones.add_row("iOS", udid)
        if phones.row_count == 0:
            phones.add_row("-", "no phones detected")
