"""Entry shim — briefcase derives the module it runs from the app name.

`me.sshlg.vibe-bridge` is the bundle identifier, and the bundle identifier is
the TCC identity (ADR-0006): change it and every permission the owner granted
is granted to a different app. Briefcase computes `module_name` from the app
name and will not let it be set, so the app stays `vibe-bridge` and this
package exists to be the module that name implies.

The shell itself is `vbboot` — kept under a visibly different name because
`vibe_bridge` (this shim) and `vibebridge` (the payload) are one underscore
apart, and the bootstrap must never be confused with the code it loads.
"""
