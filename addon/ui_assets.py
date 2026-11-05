# SPDX-License-Identifier: GPL-3.0-or-later
"""Panel for browsing and placing Poly Haven models and HDRIs."""

import bpy

from . import assets

_items = []   # keeps the enum items alive while Blender shows them


def _choice_items(self, context):
    return _items or [("NONE", "Search to see assets", "", 0, 0)]


def _run_search(self, context):
    wm = context.window_manager
    try:
        ids = assets.search(wm.bl_asset_kind, wm.bl_asset_query)
        icons = assets.load_thumbnails(ids)
    except Exception as exc:  # offline etc.
        _items.clear()
        _items.append(("NONE", f"Search failed: {exc}", "", 0, 0))
        return
    info = assets.index(wm.bl_asset_kind)
    _items.clear()
    for n, aid in enumerate(ids):
        name = info.get(aid, {}).get("name", aid)
        _items.append((aid, name, ", ".join(info.get(aid, {}).get("categories", [])[:3]), icons.get(aid, 0), n))
    if not _items:
        _items.append(("NONE", "Nothing found", "", 0, 0))


class BLENDERING_OT_asset_add(bpy.types.Operator):
    bl_idname = "blendering.asset_add"
    bl_label = "Add to scene"
    bl_description = ("Download the model (once) and stand it on the surface under the 3D cursor. "
                      "Real size, random turn; move it with G and R")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        wm = context.window_manager
        return wm.bl_asset_kind == 'models' and wm.bl_asset_choice not in {"", "NONE"}

    def execute(self, context):
        aid = context.window_manager.bl_asset_choice
        try:
            root = assets.add_model(aid, context.scene)
        except Exception as exc:
            self.report({'ERROR'}, f"Could not add {aid}: {exc}")
            return {'CANCELLED'}
        for o in context.selected_objects:
            o.select_set(False)
        root.select_set(True)
        context.view_layer.objects.active = root
        self.report({'INFO'}, f"Added {root.name}")
        return {'FINISHED'}


class BLENDERING_OT_asset_hdri(bpy.types.Operator):
    bl_idname = "blendering.asset_hdri"
    bl_label = "Use as environment"
    bl_description = ("Light the scene with this HDRI panorama and show it as the background "
                      "(sky, landscape, a view through a window)")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        wm = context.window_manager
        return wm.bl_asset_kind == 'hdris' and wm.bl_asset_choice not in {"", "NONE"}

    def execute(self, context):
        wm = context.window_manager
        try:
            assets.set_hdri(wm.bl_asset_choice, context.scene, wm.bl_hdri_strength,
                            wm.bl_hdri_rotation, visible=wm.bl_hdri_visible)
        except Exception as exc:
            self.report({'ERROR'}, f"HDRI: {exc}")
            return {'CANCELLED'}
        self.report({'INFO'}, f"Environment: {wm.bl_asset_choice}")
        return {'FINISHED'}


class BLENDERING_PT_assets(bpy.types.Panel):
    bl_label = "Assets (free 3D models, HDRIs)"
    bl_idname = "BLENDERING_PT_assets"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Blendering"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        wm = context.window_manager
        layout = self.layout
        layout.row().prop(wm, "bl_asset_kind", expand=True)
        layout.prop(wm, "bl_asset_query", text="", icon='VIEWZOOM')
        if _items and _items[0][0] != "NONE":
            layout.template_icon_view(wm, "bl_asset_choice", show_labels=True, scale=6.0, scale_popup=5.0)
            info = assets.index(wm.bl_asset_kind).get(wm.bl_asset_choice, {})
            layout.label(text=info.get("name", ""))
        else:
            layout.label(text=_items[0][1] if _items else "Type e.g. plant, vase, chair, sky", icon='INFO')
        if wm.bl_asset_kind == 'models':
            col = layout.column()
            col.scale_y = 1.3
            col.operator("blendering.asset_add", icon='ADD')
            layout.label(text="Placed at the 3D cursor (Shift+right-click)", icon='PIVOT_CURSOR')
        else:
            layout.prop(wm, "bl_hdri_strength")
            layout.prop(wm, "bl_hdri_rotation")
            layout.prop(wm, "bl_hdri_visible")
            col = layout.column()
            col.scale_y = 1.3
            col.operator("blendering.asset_hdri", icon='WORLD')
        layout.label(text="Poly Haven, CC0 (free for any use)", icon='CHECKMARK')


classes = (BLENDERING_OT_asset_add, BLENDERING_OT_asset_hdri, BLENDERING_PT_assets)


def _kind_changed(self, context):
    _run_search(self, context)


def register():
    wm = bpy.types.WindowManager
    wm.bl_asset_kind = bpy.props.EnumProperty(
        items=[('models', "3D models", "Plants, vases, books, furniture, props"),
               ('hdris', "Environments", "HDRI panoramas: skies, landscapes, interiors")],
        update=_kind_changed)
    wm.bl_asset_query = bpy.props.StringProperty(name="Search", update=_run_search,
                                                 description="English keywords, e.g. potted plant, vase, book, sky")
    wm.bl_asset_choice = bpy.props.EnumProperty(name="Asset", items=_choice_items)
    wm.bl_hdri_strength = bpy.props.FloatProperty(name="Strength", default=1.0, min=0.0, soft_max=5.0)
    wm.bl_hdri_rotation = bpy.props.FloatProperty(name="Rotation (°)", default=0.0, soft_min=-180, soft_max=180)
    wm.bl_hdri_visible = bpy.props.BoolProperty(name="Show as background", default=True)
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
    wm = bpy.types.WindowManager
    for p in ("bl_asset_kind", "bl_asset_query", "bl_asset_choice", "bl_hdri_strength",
              "bl_hdri_rotation", "bl_hdri_visible"):
        if hasattr(wm, p):
            delattr(wm, p)
    assets.unregister()
