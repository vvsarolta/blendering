# SPDX-License-Identifier: GPL-3.0-or-later
"""Blendering: a visualization toolkit for Blender.

Modules (see the project plan):
    1. Rhino -> Blender bridge          (rhino_bridge.py)
    2. Visualization tools               (studio.py, materials.py)
    3. Scene-aware AI layer              (coming)
    4. Post-processing panel             (coming)
"""

import json
import os

import bpy

from . import rhino_bridge, studio

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

    studio_preset: bpy.props.EnumProperty(
        name="Studio",
        description="Studio preset to build around the subject",
        items=lambda self, context: _preset_items(),
    )
    studio_description: bpy.props.StringProperty(
        name="Description",
        description="Describe the studio in words (used by the AI layer)",
    )
    studio_reference: bpy.props.StringProperty(
        name="Reference",
        description="Reference photo of the look you want (used by the AI layer)",
        subtype="FILE_PATH",
    )
    studio_spec: bpy.props.StringProperty(options={'HIDDEN'})  # last built spec, JSON


_preset_cache = []


def _preset_items():
    _preset_cache.clear()
    for name in studio.list_presets():
        _preset_cache.append((name, name, studio.BUILTIN_PRESETS.get(name, {}).get("description", "Saved studio")))
    return _preset_cache or [("NONE", "No presets", "")]


def _subject_objects(context):
    """Selected objects, or everything visible that is not part of the studio."""
    studio_coll = bpy.data.collections.get(studio.COLL_NAME)
    skip = set(studio_coll.all_objects) if studio_coll else set()
    sel = [o for o in context.selected_objects if o not in skip]
    if sel:
        return sel
    return [o for o in context.scene.objects
            if o not in skip and o.visible_get() and o.type in {'MESH', 'EMPTY'}]


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


class BLENDERING_OT_build_studio(bpy.types.Operator):
    bl_idname = "blendering.build_studio"
    bl_label = "Build studio"
    bl_description = ("Build the chosen studio around the selected objects "
                      "(or around everything visible). Replaces the previous studio")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        s = context.scene.blendering
        try:
            spec = studio.load_preset(s.studio_preset)
            studio.build(spec, _subject_objects(context), context.scene)
        except Exception as exc:
            self.report({'ERROR'}, f"Studio: {exc}")
            return {'CANCELLED'}
        s.studio_spec = json.dumps(spec)
        self.report({'INFO'}, f"Studio built: {s.studio_preset}")
        return {'FINISHED'}


class BLENDERING_OT_save_studio(bpy.types.Operator):
    bl_idname = "blendering.save_studio"
    bl_label = "Save as preset"
    bl_description = "Save the last built studio as a preset you can reuse in any project"

    name: bpy.props.StringProperty(name="Name", default="My studio")

    @classmethod
    def poll(cls, context):
        return bool(context.scene.blendering.studio_spec)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        studio.save_preset(self.name, json.loads(context.scene.blendering.studio_spec))
        context.scene.blendering.studio_preset = self.name
        self.report({'INFO'}, f"Saved studio preset: {self.name}")
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
        box.prop(s, "studio_preset", text="")
        row = box.row()
        row.scale_y = 1.4
        row.operator("blendering.build_studio", icon="SCENE")
        box.operator("blendering.save_studio", icon="FILE_TICK")
        sub = box.column(align=True)
        sub.label(text="From description or photo (AI layer):")
        sub.prop(s, "studio_description", text="", icon="TEXT")
        sub.prop(s, "studio_reference", text="")

        box = layout.box()
        box.label(text="AI layer", icon="SHADERFX")
        box.label(text="Coming in phase 2", icon="TIME")


classes = (
    BlenderingPreferences,
    BlenderingSceneSettings,
    BLENDERING_OT_sync,
    BLENDERING_OT_build_studio,
    BLENDERING_OT_save_studio,
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
