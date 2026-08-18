"""Composable views making up the DWGMAGIC window.

Each view owns its own widgets and exposes a small imperative API plus
callbacks; ``gui/app.py`` wires them together and routes pipeline events to
them. Splitting them out is what keeps the window class from growing back
into the 1,500-line control-panel-plus-log-viewer it used to be.
"""
