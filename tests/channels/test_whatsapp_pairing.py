"""Tests for the WhatsApp pairing event/decision logic (Phase 8 #1)."""

from __future__ import annotations

import json

from openjarvis.channels import whatsapp_pairing as wp
from openjarvis.channels.whatsapp_pairing import PairingState


class TestParseBridgeLine:
    def test_parses_json_object(self):
        assert wp.parse_bridge_line('{"type":"qr","data":"abc"}') == {"type": "qr", "data": "abc"}

    def test_blank_and_non_json_return_none(self):
        assert wp.parse_bridge_line("") is None
        assert wp.parse_bridge_line("   ") is None
        assert wp.parse_bridge_line("not json at all") is None

    def test_json_array_is_not_an_event(self):
        assert wp.parse_bridge_line("[1,2,3]") is None


class TestPairingState:
    def test_qr_then_connected_is_paired(self):
        s = PairingState()
        s.apply({"type": "qr", "data": "x"})
        assert s.qr_shown and not s.done
        s.apply({"type": "self", "jid": "44777@s.whatsapp.net"})
        s.apply({"type": "status", "status": "connected"})
        assert s.connected and s.done
        assert s.jid == "44777@s.whatsapp.net"

    def test_error_event_terminates(self):
        s = PairingState()
        s.apply({"type": "error", "message": "Logged out from WhatsApp"})
        assert s.done and not s.connected
        assert "Logged out" in (s.error or "")

    def test_non_connected_status_does_not_finish(self):
        s = PairingState()
        s.apply({"type": "status", "status": "disconnected"})
        assert not s.done

    def test_drives_from_raw_lines(self):
        s = PairingState()
        lines = [
            '{"type":"qr","data":"x"}',
            "noise that is not json",
            '{"type":"status","status":"connecting"}',
            '{"type":"status","status":"connected"}',
        ]
        for line in lines:
            ev = wp.parse_bridge_line(line)
            if ev:
                s.apply(ev)
        assert s.connected and s.qr_shown


class TestPreflight:
    def test_missing_node_fails(self, monkeypatch):
        monkeypatch.setattr(wp.shutil, "which", lambda name: None)
        result = wp.preflight(build_if_missing=False)
        assert not result.ok
        assert "node" in result.reason.lower()

    def test_present_compiled_bridge_passes(self, monkeypatch, tmp_path):
        monkeypatch.setattr(wp.shutil, "which", lambda name: f"/usr/bin/{name}")
        fake_bridge = tmp_path / "dist" / "bridge.js"
        fake_bridge.parent.mkdir(parents=True)
        fake_bridge.write_text("// bridge", encoding="utf-8")
        monkeypatch.setattr(wp, "_BRIDGE_SRC", tmp_path)
        result = wp.preflight(build_if_missing=False)
        assert result.ok
        assert result.bridge_js == fake_bridge

    def test_missing_bridge_without_build_fails(self, monkeypatch, tmp_path):
        monkeypatch.setattr(wp.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(wp, "_BRIDGE_SRC", tmp_path)
        result = wp.preflight(build_if_missing=False)
        assert not result.ok
        assert "not compiled" in result.reason.lower()


class TestRunPairingGuards:
    def test_preflight_failure_short_circuits(self, monkeypatch):
        monkeypatch.setattr(
            wp, "preflight", lambda build_if_missing=True: wp.PreflightResult(False, "no node")
        )
        result = wp.run_pairing(timeout_s=1)
        assert result["paired"] is False
        assert result["reason"] == "no node"
