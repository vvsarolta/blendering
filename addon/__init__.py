# SPDX-License-Identifier: GPL-3.0-or-later
"""Blendering: a visualization toolkit for Blender.

Modules (see the project plan):
    1. Rhino -> Blender bridge
    2. Visualization tools (materials, studio builder, presets)
    3. Scene-aware AI layer
    4. Post-processing panel
"""

import bpy


class BlenderingPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    watch_folder: bpy.props.StringProperty(
        name="Watched folder",
        description="Folder where Rhino saves the .3dm files to sync",
        subtype="DIR_PATH",
    )

    ai_api_key: bpy.props.StringProperty(
        name="AI API key",
        description="Key for the cloud AI service used by the AI layer",
        subtype="PASSWORD",
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "watch_folder")
        layout.prop(self, "ai_api_key")


class BLENDERING_PT_main(bpy.types.Panel):
    bl_label = "Blendering"
    bl_idname = "BLENDERING_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Blendering"

    def draw(self, context):
        layout = self.layout
        prefs = context.preferences.addons[__package__].preferences

        box = layout.box()
        box.label(text="Rhino bridge", icon="LINKED")
        folder = prefs.watch_folder or "not set"
        box.label(text=f"Folder: {folder}")
        box.label(text="Sync: coming in phase 1", icon="INFO")

        box = layout.box()
        box.label(text="Studio builder", icon="LIGHT_AREA")
        box.label(text="Coming in phase 1", icon="INFO")

        box = layout.box()
        box.label(text="AI layer", icon="SHADERFX")
        box.label(text="Coming in phase 2", icon="INFO")


classes = (
    BlenderingPreferences,
    BLENDERING_PT_main,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
