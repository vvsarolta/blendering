# SPDX-License-Identifier: GPL-3.0-or-later
"""Blendering: a visualization toolkit for Blender.

Modules (see the project plan):
    1. Rhino -> Blender bridge          (rhino_bridge.py)
    2. Visualization tools               (coming)
    3. Scene-aware AI layer              (coming)
    4. Post-processing panel             (coming)
"""

import os

import bpy

from . import rhino_bridge

WATCH_INTERVAL = 2.0  # seconds between file checks when auto-sync is on


# ------------------------------------------------------------ preferences

class BlenderingPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    ai_api_key: bpy.props.StringProperty(
        name="AI API key",
        description="Key for the cloud AI service used by the AI layer",
        subtype="PASSWORD",
    )

    def draw(self, context):
        self.layout.prop(self, "ai_api_key")


# ---------------------------------------------------------- scene settings

def _auto_sync_changed(self, context):
    if self.auto_sync:
        self.last_mtime = 0.0
        if not bpy.app.timers.is_registered(_watch_tick):
            bpy.app.timers.register(_watch_tick, first_interval=0.1)


class BlenderingSceneSettings(bpy.types.PropertyGroup):
    rhino_file: bpy.props.StringProperty(
        name="Rhino file",
        description="The .3dm file to mirror into this scene",
        subtype="FILE_PATH",
    )
    auto_sync: bpy.props.BoolProperty(
        name="Auto-sync on save",
        description="Re-sync every time Rhino saves the file",
        default=False,
        update=_auto_sync_changed,
    )
    last_mtime: bpy.props.FloatProperty(options={'HIDDEN'})
    last_report: bpy.props.StringProperty(options={'HIDDEN'})


def _run_sync(scene):
    settings = scene.blendering
    report = rhino_bridge.sync(settings.rhino_file, scene)
    path = bpy.path.abspath(settings.rhino_file)
    settings.last_mtime = os.path.getmtime(path)
    settings.last_report = report.summary()
    return report


# ------------------------------------------------------------ auto-sync

def _watch_tick():
    """Timer: re-sync any scene whose Rhino file changed on disk."""
    any_watching = False
    for scene in bpy.data.scenes:
        s = getattr(scene, "blendering", None)
        if s is None or not s.auto_sync or not s.rhino_file:
            continue
        any_watching = True
        path = bpy.path.abspath(s.rhino_file)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if mtime == s.last_mtime:
            _pending.pop(scene.name, None)
            continue
        # Rhino may still be writing: only sync once the file has been
        # unchanged for one full interval.
        if _pending.get(scene.name) != mtime:
            _pending[scene.name] = mtime
            continue
        _pending.pop(scene.name, None)
        try:
            _run_sync(scene)
        except Exception as exc:  # keep the timer alive, retry on next save
            s.last_report = f"Sync failed: {exc}"
            s.last_mtime = mtime
    return WATCH_INTERVAL if any_watching else None


_pending = {}


@bpy.app.handlers.persistent
def _on_load(_dummy):
    for scene in bpy.data.scenes:
        s = getattr(scene, "blendering", None)
        if s is not None and s.auto_sync:
            if not bpy.app.timers.is_registered(_watch_tick):
                bpy.app.timers.register(_watch_tick, first_interval=1.0)
            break


# --------------------------------------------------------------- operator

class BLENDERING_OT_sync(bpy.types.Operator):
    bl_idname = "blendering.sync"
    bl_label = "Sync from Rhino"
    bl_description = "Read the Rhino file and update the scene, keeping Blender materials"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return bool(context.scene.blendering.rhino_file)

    def execute(self, context):
        try:
            report = _run_sync(context.scene)
        except rhino_bridge.BridgeError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, "Rhino sync: " + report.summary())
        for line in report.skipped[:10]:
            self.report({'WARNING'}, line)
        return {'FINISHED'}


# ------------------------------------------------------------------ panel

class BLENDERING_PT_main(bpy.types.Panel):
    bl_label = "Blendering"
    bl_idname = "BLENDERING_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Blendering"

    def draw(self, context):
        layout = self.layout
        s = context.scene.blendering

        box = layout.box()
        box.label(text="Rhino bridge", icon="LINKED")
        box.prop(s, "rhino_file", text="")
        row = box.row()
        row.scale_y = 1.4
        row.operator("blendering.sync", icon="FILE_REFRESH")
        box.prop(s, "auto_sync")
        if s.last_report:
            box.label(text=s.last_report, icon="INFO")

        box = layout.box()
        box.label(text="Studio builder", icon="LIGHT_AREA")
        box.label(text="Coming in phase 1", icon="TIME")

        box = layout.box()
        box.label(text="AI layer", icon="SHADERFX")
        box.label(text="Coming in phase 2", icon="TIME")


classes = (
    BlenderingPreferences,
    BlenderingSceneSettings,
    BLENDERING_OT_sync,
    BLENDERING_PT_main,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.blendering = bpy.props.PointerProperty(type=BlenderingSceneSettings)
    bpy.app.handlers.load_post.append(_on_load)


def unregister():
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    if bpy.app.timers.is_registered(_watch_tick):
        bpy.app.timers.unregister(_watch_tick)
    del bpy.types.Scene.blendering
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
