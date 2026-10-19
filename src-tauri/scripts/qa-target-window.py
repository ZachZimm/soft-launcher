#!/usr/bin/env python3
"""Harmless fixed-size window used as the click-outside target in isolated QA."""
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib
GLib.set_prgname('soft-launcher-qa-target')
w = Gtk.Window(title='qa target')
w.set_default_size(320, 200)
w.set_size_request(320, 200)
w.set_resizable(False)
w.add(Gtk.Label(label='soft-launcher QA click target'))
w.connect('destroy', Gtk.main_quit)
w.show_all()
GLib.timeout_add_seconds(900, Gtk.main_quit)
Gtk.main()
