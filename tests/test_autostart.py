"""Launch at login, including every way it can be unavailable.

The bridge runs on three platforms and two of them have no Login Items API at
all, so "unsupported" is a normal answer here rather than an error path. What
must never happen is the panel showing a switch that does nothing: each
failure carries a reason the owner can act on.
"""
from __future__ import annotations

from vibebridge import autostart


class _Service:
    def __init__(self, status=1, ok=True, err=None):
        self._status, self._ok, self._err = status, ok, err
        self.registered = self.unregistered = False

    def status(self):
        return self._status

    def registerAndReturnError_(self, _):
        self.registered = True
        return self._ok, self._err

    def unregisterAndReturnError_(self, _):
        self.unregistered = True
        return self._ok, self._err


class _SM:
    """Stand-in for the ServiceManagement bridge."""

    def __init__(self, service):
        self._service = service
        self.opened = False
        outer = self

        class SMAppService:
            @staticmethod
            def mainAppService():
                return outer._service

            @staticmethod
            def openSystemSettingsLoginItems():
                outer.opened = True

        self.SMAppService = SMAppService


class _Error:
    @staticmethod
    def localizedDescription():
        return "операция не разрешена"


def test_enabled_status_is_reported_in_words():
    st = autostart.status(_SM(_Service(status=1)))
    assert st.state == "enabled" and st.supported
    assert "стартует при входе" in st.human


def test_awaiting_approval_is_not_an_error():
    """macOS asks the owner; the panel must say that, not "failed"."""
    st = autostart.status(_SM(_Service(status=2)))
    assert st.state == "requires-approval"
    assert "Login Items" in st.human


def test_not_installed_is_distinguished_from_switched_off():
    assert autostart.status(_SM(_Service(status=3))).state == "not-found"
    assert autostart.status(_SM(_Service(status=0))).state == "not-registered"


def test_enable_registers_and_reports_the_new_state():
    svc = _Service(status=1)
    ok, msg = autostart.enable(_SM(svc))
    assert ok and svc.registered and "стартует при входе" in msg


def test_disable_unregisters():
    svc = _Service(status=0)
    ok, msg = autostart.disable(_SM(svc))
    assert ok and svc.unregistered and "выключен" in msg


def test_refusal_from_macos_carries_the_system_reason():
    ok, msg = autostart.enable(_SM(_Service(ok=False, err=_Error())))
    assert not ok and "не разрешена" in msg


def test_framework_raising_is_reported_not_propagated():
    class Exploding:
        class SMAppService:
            @staticmethod
            def mainAppService():
                raise RuntimeError("bridge died")

    st = autostart.status(Exploding())
    assert st.state == "unsupported" and not st.supported
    ok, msg = autostart.enable(Exploding())
    assert not ok and "bridge died" in msg


def test_missing_api_on_older_macos_is_named_precisely():
    class NoAPI:
        pass

    st = autostart.status(NoAPI())
    # An object with no SMAppService still must not raise on the enable path.
    ok, msg = autostart.enable(NoAPI())
    assert not ok and msg
    assert st.state == "unsupported"


def test_open_settings_hands_the_switch_to_the_owner():
    sm = _SM(_Service())
    assert autostart.open_settings(sm) is True
    assert sm.opened is True


class _State:
    def __init__(self, registered=False):
        self.autostart_registered = registered
        self.saved = False

    def save(self):
        self.saved = True


def test_first_launch_registers_and_remembers_it():
    state, svc = _State(), _Service(status=1)
    ok, _ = autostart.ensure_registered(state, _SM(svc))
    assert ok and svc.registered
    assert state.autostart_registered and state.saved


def test_second_launch_does_not_ask_again():
    """The owner may have switched it off; re-registering would override
    them, and a switch the app keeps flipping back is not a switch."""
    state, svc = _State(registered=True), _Service(status=0)
    ok, why = autostart.ensure_registered(state, _SM(svc))
    assert ok is None and not svc.registered      # None, not False: no failure
    assert "решение за владельцем" in why


def test_nothing_to_do_is_not_the_same_as_failed():
    """Every ordinary launch hits this path. Reporting it as a failure puts
    a red mark on normal behaviour and trains the owner to ignore red."""
    settled, _ = autostart.ensure_registered(_State(registered=True),
                                             _SM(_Service()))
    failed, _ = autostart.ensure_registered(_State(),
                                            _SM(_Service(ok=False,
                                                         err=_Error())))
    assert settled is None
    assert failed is False


def test_a_failed_registration_is_not_remembered_as_done():
    """Otherwise one bad launch permanently disables autostart setup."""
    state = _State()
    ok, _ = autostart.ensure_registered(state, _SM(_Service(ok=False,
                                                            err=_Error())))
    assert not ok and not state.autostart_registered


def test_not_found_is_not_reported_as_not_installed():
    st = autostart.status(_SM(_Service(status=3)))
    assert "не установлено" not in st.human
