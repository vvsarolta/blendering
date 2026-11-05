# SPDX-License-Identifier: GPL-3.0-or-later
"""Panel and operators for the post-processing layer stack."""

import os

import bpy

from . import post


def _subject(context):
    from . import studio
    coll = bpy.data.collections.get(studio.COLL_NAME)
    skip = set(coll.all_objects) if coll else set()
    sel = [o for o in context.selected_objects if o not in skip and o.type in {'MESH', 'EMPTY'}]
    if sel:
        return sel
    tagged = [o for o in context.scene.objects if o.get("rhino_file") and o.type in {'MESH', 'EMPTY'}]
    return tagged or [o for o in context.scene.objects if o not in skip and o.type == 'MESH' and o.visible_get()]


def _live_preview(context):
    for area in context.screen.areas if context.screen else []:
        if area.type == 'VIEW_3D':
            area.spaces.active.shading.use_compositor = 'ALWAYS'


class BLENDERING_UL_post_layers(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        label, icon_name = next(((t[1], t[3]) for t in post.LAYER_TYPES if t[0] == item.kind), ("?", 'DOT'))
        row = layout.row(align=True)
        row.prop(item, "enabled", text="", emboss=False,
                 icon='HIDE_OFF' if item.enabled else 'HIDE_ON')
        row.label(text=label, icon=icon_name)
        if item.mask != 'ALL':
            row.label(text="product" if item.mask == 'PRODUCT' else "background")
        if item.opacity < 1.0:
            row.label(text=f"{int(round(item.opacity * 100))}%")


class BLENDERING_OT_post_add(bpy.types.Operator):
    bl_idname = "blendering.post_add"
    bl_label = "Add adjustment layer"
    bl_options = {'REGISTER', 'UNDO'}

    kind: bpy.props.EnumProperty(items=[t[:4] for t in post.LAYER_TYPES])

    def execute(self, context):
        p = context.scene.blendering_post
        layer = p.layers.add()
        layer.kind = self.kind
        layer.amount = post.DEFAULT_AMOUNT.get(self.kind, 0.2)
        if self.kind == 'VIGNETTE':
            layer.mask = 'ALL'
        p.active = len(p.layers) - 1
        post.build_group(context.scene)
        _live_preview(context)
        return {'FINISHED'}


class BLENDERING_MT_post_add(bpy.types.Menu):
    bl_idname = "BLENDERING_MT_post_add"
    bl_label = "Add layer"

    def draw(self, context):
        for kind, label, desc, icon in post.LAYER_TYPES:
            self.layout.operator("blendering.post_add", text=label, icon=icon).kind = kind


class BLENDERING_OT_post_remove(bpy.types.Operator):
    bl_idname = "blendering.post_remove"
    bl_label = "Remove layer"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return len(context.scene.blendering_post.layers) > 0

    def execute(self, context):
        p = context.scene.blendering_post
        p.layers.remove(p.active)
        p.active = max(0, min(p.active, len(p.layers) - 1))
        post.build_group(context.scene)
        return {'FINISHED'}


class BLENDERING_OT_post_move(bpy.types.Operator):
    bl_idname = "blendering.post_move"
    bl_label = "Move layer"
    bl_options = {'REGISTER', 'UNDO'}

    direction: bpy.props.EnumProperty(items=[('UP', "Up", ""), ('DOWN', "Down", "")])

    def execute(self, context):
        p = context.scene.blendering_post
        i = p.active
        j = i - 1 if self.direction == 'UP' else i + 1
        if 0 <= j < len(p.layers):
            p.layers.move(i, j)
            p.active = j
            post.build_group(context.scene)
        return {'FINISHED'}


class BLENDERING_OT_post_tag_product(bpy.types.Operator):
    bl_idname = "blendering.post_tag_product"
    bl_label = "Set product"
    bl_description = ("Mark the selected objects (or the Rhino model) as the product, "
                      "for layers applied to the product or the background only")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        for o in context.scene.objects:
            if o.pass_index == post.PRODUCT_INDEX:
                o.pass_index = 0
        objs = _subject(context)
        post.tag_product(context.scene, objs)
        post.build_group(context.scene)
        self.report({'INFO'}, f"Product: {len(objs)} objects")
        return {'FINISHED'}


class BLENDERING_OT_post_export(bpy.types.Operator):
    bl_idname = "blendering.post_export"
    bl_label = "Export image"
    bl_description = "Save the last render, with all post-processing layers, as an image file"

    filepath: bpy.props.StringProperty(subtype='FILE_PATH')
    fmt: bpy.props.EnumProperty(name="Format", items=[
        ('PNG16', "PNG 16-bit", "Lossless, for further editing"),
        ('PNG8', "PNG 8-bit", "Lossless, smaller"),
        ('JPEG', "JPG", "For web and email"),
        ('TIFF16', "TIFF 16-bit", "For print and retouching"),
    ], default='PNG16')

    @classmethod
    def poll(cls, context):
        img = bpy.data.images.get("Render Result")
        return img is not None and img.has_data

    def invoke(self, context, event):
        base = bpy.path.abspath("//") or os.path.expanduser("~")
        self.filepath = os.path.join(base, "renders", (bpy.path.display_name_from_filepath(bpy.data.filepath) or "render"))
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        scene = context.scene
        s = scene.render.image_settings
        old = (s.file_format, s.color_depth, s.quality)
        ext = {'PNG16': ".png", 'PNG8': ".png", 'JPEG': ".jpg", 'TIFF16': ".tif"}[self.fmt]
        s.file_format = {'PNG16': 'PNG', 'PNG8': 'PNG', 'JPEG': 'JPEG', 'TIFF16': 'TIFF'}[self.fmt]
        if self.fmt in {'PNG16', 'TIFF16'}:
            s.color_depth = '16'
        elif self.fmt == 'PNG8':
            s.color_depth = '8'
        else:
            s.quality = 92
        path = os.path.splitext(bpy.path.abspath(self.filepath))[0] + ext
        os.makedirs(os.path.dirname(path), exist_ok=True)
        bpy.data.images["Render Result"].save_render(path, scene=scene)
        s.file_format, s.color_depth, s.quality = old[0], old[1] if old[0] in {'PNG', 'TIFF'} else s.color_depth, old[2]
        self.report({'INFO'}, f"Saved {path}")
        return {'FINISHED'}


class BLENDERING_PT_post(bpy.types.Panel):
    bl_label = "Post-processing"
    bl_idname = "BLENDERING_PT_post"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Blendering"

    def draw_header(self, context):
        self.layout.prop(context.scene.blendering_post, "enabled", text="")

    def draw(self, context):
        layout = self.layout
        p = context.scene.blendering_post
        row = layout.row()
        row.template_list("BLENDERING_UL_post_layers", "", p, "layers", p, "active", rows=4)
        col = row.column(align=True)
        col.menu("BLENDERING_MT_post_add", text="", icon='ADD')
        col.operator("blendering.post_remove", text="", icon='REMOVE')
        col.separator()
        col.operator("blendering.post_move", text="", icon='TRIA_UP').direction = 'UP'
        col.operator("blendering.post_move", text="", icon='TRIA_DOWN').direction = 'DOWN'

        if p.layers and 0 <= p.active < len(p.layers):
            layer = p.layers[p.active]
            box = layout.box()
            if layer.kind == 'CURVES':
                node = post.curve_node_for(context.scene, layer)
                if node is not None:
                    box.template_curve_mapping(node, "mapping", type='COLOR')
            else:
                box.prop(layer, "amount", slider=True)
            box.prop(layer, "opacity", slider=True)
            box.prop(layer, "mask")
            if layer.mask != 'ALL':
                box.operator("blendering.post_tag_product", icon='RESTRICT_SELECT_OFF')

        layout.separator()
        layout.operator("blendering.post_export", icon='EXPORT')
        layout.label(text="Live preview: Rendered view", icon='INFO')


classes = (
    post.BlenderingPostLayer,
    post.BlenderingPostSettings,
    BLENDERING_UL_post_layers,
    BLENDERING_OT_post_add,
    BLENDERING_MT_post_add,
    BLENDERING_OT_post_remove,
    BLENDERING_OT_post_move,
    BLENDERING_OT_post_tag_product,
    BLENDERING_OT_post_export,
    BLENDERING_PT_post,
)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.blendering_post = bpy.props.PointerProperty(type=post.BlenderingPostSettings)


def unregister():
    del bpy.types.Scene.blendering_post
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
