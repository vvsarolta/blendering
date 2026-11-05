# SPDX-License-Identifier: GPL-3.0-or-later
"""Panel for Ask Claude."""

import bpy

from . import ai


def _tick():
    for w in bpy.context.window_manager.windows:
        for a in w.screen.areas:
            if a.type == 'VIEW_3D':
                a.tag_redraw()
    return 0.5 if ai.state()["running"] else None


class BLENDERING_OT_ai_ask(bpy.types.Operator):
    bl_idname = "blendering.ai_ask"
    bl_label = "Ask Claude"
    bl_description = "Send the request and a summary of the scene to Claude on this computer"

    @classmethod
    def poll(cls, context):
        return bool(context.window_manager.bl_ai_request.strip()) and not ai.state()["running"]

    def execute(self, context):
        wm = context.window_manager
        try:
            ai.ask(wm.bl_ai_request, context.scene, "" if wm.bl_ai_model == "DEFAULT" else wm.bl_ai_model)
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        if not bpy.app.timers.is_registered(_tick):
            bpy.app.timers.register(_tick, first_interval=0.5)
        return {'FINISHED'}


class BLENDERING_OT_ai_apply(bpy.types.Operator):
    bl_idname = "blendering.ai_apply"
    bl_label = "Apply"
    bl_description = "Run Claude's script on the scene (Cmd+Z undoes it)"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        st = ai.state()
        return bool(st["code"]) and not st["running"]

    def execute(self, context):
        try:
            ai.apply(ai.state()["code"], context.scene)
        except Exception as exc:
            self.report({'ERROR'}, f"The script failed: {exc}")
            return {'CANCELLED'}
        ai.clear()
        context.window_manager.bl_ai_request = ""
        self.report({'INFO'}, "Applied. Cmd+Z to undo")
        return {'FINISHED'}


class BLENDERING_OT_ai_discard(bpy.types.Operator):
    bl_idname = "blendering.ai_discard"
    bl_label = "Discard"

    def execute(self, context):
        ai.clear()
        return {'FINISHED'}


class BLENDERING_OT_ai_show_script(bpy.types.Operator):
    bl_idname = "blendering.ai_show_script"
    bl_label = "Open script in Text Editor"
    bl_description = "Show the whole script in Blender's Text Editor (Scripting tab)"

    def execute(self, context):
        txt = bpy.data.texts.get("Claude script") or bpy.data.texts.new("Claude script")
        txt.clear()
        txt.write(ai.state()["code"])
        self.report({'INFO'}, "Script is in the Text Editor as 'Claude script'")
        return {'FINISHED'}


class BLENDERING_PT_ai(bpy.types.Panel):
    bl_label = "Ask Claude"
    bl_idname = "BLENDERING_PT_ai"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Blendering"

    def draw(self, context):
        layout = self.layout
        wm = context.window_manager
        if ai.find_claude() is None:
            box = layout.box()
            box.label(text="Claude Code is not installed", icon='ERROR')
            box.label(text="1. Terminal: curl -fsSL https://claude.ai/install.sh | bash")
            box.label(text="2. Then run: claude   and sign in with your Claude account")
            box.label(text="3. Restart Blender")
            return
        layout.prop(wm, "bl_ai_request", text="", icon='OUTLINER_DATA_GP_LAYER')
        row = layout.row(align=True)
        row.scale_y = 1.3
        row.operator("blendering.ai_ask", icon='PLAY')
        row.prop(wm, "bl_ai_model", text="")
        st = ai.state()
        if st["running"]:
            layout.label(text="Claude is thinking…", icon='SORTTIME')
            return
        if st["error"]:
            box = layout.box()
            box.label(text="Error", icon='ERROR')
            for line in st["error"].splitlines()[-4:]:
                box.label(text=line[:90])
        if st["code"]:
            box = layout.box()
            notes = [l[1:].strip() for l in st["code"].splitlines() if l.startswith("#")][:4]
            for n in notes or ["(no description)"]:
                box.label(text=n[:90])
            box.label(text=f"{len(st['code'].splitlines())} lines of Python", icon='SCRIPT')
            row = box.row(align=True)
            row.scale_y = 1.3
            row.operator("blendering.ai_apply", icon='CHECKMARK')
            row.operator("blendering.ai_discard", icon='X')
            box.operator("blendering.ai_show_script", icon='TEXT')
        layout.label(text="Uses your Claude plan via Claude Code", icon='INFO')


classes = (BLENDERING_OT_ai_ask, BLENDERING_OT_ai_apply, BLENDERING_OT_ai_discard,
           BLENDERING_OT_ai_show_script, BLENDERING_PT_ai)


def register():
    wm = bpy.types.WindowManager
    wm.bl_ai_request = bpy.props.StringProperty(
        name="Request", description="What should change? E.g. 'tegyél egy vázát a szék mellé'")
    wm.bl_ai_model = bpy.props.EnumProperty(name="Model", items=[
        ('DEFAULT', "Default", "The default model of your plan"),
        ('sonnet', "Sonnet", "Fast"),
        ('opus', "Opus", "Most capable, uses more of your plan"),
    ])
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
    for p in ("bl_ai_request", "bl_ai_model"):
        if hasattr(bpy.types.WindowManager, p):
            delattr(bpy.types.WindowManager, p)
