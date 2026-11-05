# SPDX-License-Identifier: GPL-3.0-or-later
"""Post-processing: a Photoshop-style adjustment-layer stack on top of the render.

The stack lives in the scene (``scene.blendering_post``) and is turned into a
compositor node group every time it changes, so it is non-destructive: every new
render and the Rendered viewport go through the same layers.

Each layer can be limited to the product or to the background. The masks come
from the Object Index pass: objects of the subject get pass index 1.
"""

import bpy

GROUP_NAME = "Blendering · Post"
PRODUCT_INDEX = 1

LAYER_TYPES = [
    ('EXPOSURE', "Exposure", "Brighten or darken in photographic stops", 'LIGHT_SUN'),
    ('CONTRAST', "Contrast", "Brightness and contrast", 'IMAGE_ZDEPTH'),
    ('CURVES', "Curves", "Tone curve, edited right in the panel", 'IPO_EASE_IN_OUT'),
    ('SATURATION', "Saturation", "Colour strength", 'COLOR'),
    ('WARMTH', "Warmth", "Cooler or warmer colour", 'LIGHT_DATA'),
    ('VIGNETTE', "Vignette", "Darken towards the edges", 'CLIPUV_HLT'),
    ('GLOW', "Glow", "Soft bloom around bright areas", 'LIGHT_HEMI'),
    ('SHARPEN', "Sharpen", "Crisper details", 'SHARPCURVE'),
]

MASK_ITEMS = [
    ('ALL', "Whole image", ""),
    ('PRODUCT', "Product only", "Only the pixels of the product"),
    ('BACKGROUND', "Background only", "Everything except the product"),
]


def _rebuild(self=None, context=None):
    scene = context.scene if context else bpy.context.scene
    build_group(scene)


class BlenderingPostLayer(bpy.types.PropertyGroup):
    kind: bpy.props.EnumProperty(items=[(*t, i) for i, t in enumerate(LAYER_TYPES)], update=_rebuild)
    enabled: bpy.props.BoolProperty(name="On", default=True, update=_rebuild)
    opacity: bpy.props.FloatProperty(name="Opacity", min=0.0, max=1.0, default=1.0,
                                     subtype='FACTOR', update=_rebuild)
    mask: bpy.props.EnumProperty(name="Apply to", items=MASK_ITEMS, default='ALL', update=_rebuild)
    amount: bpy.props.FloatProperty(name="Amount", default=0.0, soft_min=-1.0, soft_max=1.0,
                                    update=_rebuild,
                                    description="Strength of the effect; 0 = no change")
    curve_node: bpy.props.StringProperty(options={'HIDDEN'})  # name of its curves node


class BlenderingPostSettings(bpy.types.PropertyGroup):
    layers: bpy.props.CollectionProperty(type=BlenderingPostLayer)
    active: bpy.props.IntProperty(default=0)
    enabled: bpy.props.BoolProperty(name="Post-processing", default=True, update=_rebuild)


DEFAULT_AMOUNT = {'EXPOSURE': 0.3, 'CONTRAST': 0.2, 'CURVES': 1.0, 'SATURATION': 0.15,
                  'WARMTH': 0.15, 'VIGNETTE': 0.35, 'GLOW': 0.3, 'SHARPEN': 0.3}


# ------------------------------------------------------------------ masks

def tag_product(scene, objects):
    """Give the product objects pass index 1 and switch the Object Index pass on."""
    for vl in scene.view_layers:
        vl.use_pass_object_index = True
    for o in objects:
        o.pass_index = PRODUCT_INDEX
        if o.instance_type == 'COLLECTION' and o.instance_collection:
            for c in o.instance_collection.all_objects:
                c.pass_index = PRODUCT_INDEX


# ------------------------------------------------------------- node group

def _sock(sockets, name, kind=None):
    """Socket by name (and type), for nodes that repeat names per data type."""
    for s in sockets:
        if s.name == name and (kind is None or s.type == kind):
            return s
    raise KeyError(name)


def _menu(node, name, *values):
    """Set a menu input to the first of ``values`` the node accepts."""
    for v in values:
        try:
            node.inputs[name].default_value = v
            return True
        except (TypeError, ValueError, KeyError):
            continue
    return False


def _mix(nt, a, b, factor_socket=None, factor=1.0, x=0, y=0):
    m = nt.nodes.new("ShaderNodeMix")
    m.data_type = 'RGBA'
    m.location = (x, y)
    fac = _sock(m.inputs, "Factor", 'VALUE')
    fac.default_value = factor
    if factor_socket is not None:
        nt.links.new(factor_socket, fac)
    nt.links.new(a, _sock(m.inputs, "A", 'RGBA'))
    nt.links.new(b, _sock(m.inputs, "B", 'RGBA'))
    return _sock(m.outputs, "Result", 'RGBA')


def _math(nt, op, a, b=None, value=None, x=0, y=0):
    n = nt.nodes.new("ShaderNodeMath")
    n.operation = op
    n.location = (x, y)
    nt.links.new(a, n.inputs[0])
    if b is not None:
        nt.links.new(b, n.inputs[1])
    elif value is not None:
        n.inputs[1].default_value = value
    return n.outputs[0]


def _effect(nt, layer, img, x, y):
    """Return the output socket of ``layer``'s effect applied to ``img``."""
    k, a = layer.kind, layer.amount
    if k == 'EXPOSURE':
        n = nt.nodes.new("CompositorNodeExposure")
        n.inputs["Exposure"].default_value = a * 2.0
    elif k == 'CONTRAST':
        n = nt.nodes.new("CompositorNodeBrightContrast")
        n.inputs["Contrast"].default_value = a * 40.0
    elif k == 'SATURATION':
        n = nt.nodes.new("CompositorNodeHueSat")
        n.inputs["Saturation"].default_value = max(0.0, 1.0 + a)
    elif k == 'WARMTH':
        n = nt.nodes.new("CompositorNodeColorBalance")
        w = a * 0.15
        _sock(n.inputs, "Gain", 'RGBA').default_value = (1.0 + w, 1.0 + w * 0.3, 1.0 - w, 1.0)
    elif k == 'CURVES':
        n = nt.nodes.new("CompositorNodeCurveRGB")
        n.name = layer.curve_node or n.name
        _restore_curve(layer, n)
    elif k == 'GLOW':
        n = nt.nodes.new("CompositorNodeGlare")
        _menu(n, "Type", "Bloom", "BLOOM")
        n.inputs["Strength"].default_value = min(1.0, max(a, 0.0))
        n.inputs["Threshold"].default_value = 0.8
        n.inputs["Size"].default_value = 0.7
    elif k == 'SHARPEN':
        n = nt.nodes.new("CompositorNodeFilter")
        _menu(n, "Type", "Sharpen Diamond", "Diamond Sharpen", "Sharpen")
        n.inputs["Factor"].default_value = min(1.0, max(a, 0.0))
    elif k == 'VIGNETTE':
        ell = nt.nodes.new("CompositorNodeEllipseMask")
        ell.location = (x, y - 250)
        ell.inputs["Size"].default_value = (0.95, 0.95)
        blur = nt.nodes.new("CompositorNodeBlur")
        blur.location = (x + 180, y - 250)
        blur.inputs["Size"].default_value = (350, 350)
        nt.links.new(ell.outputs["Mask"], blur.inputs["Image"])
        inv = _math(nt, 'SUBTRACT', blur.outputs["Image"], value=0.0, x=x + 360, y=y - 250)
        inv.node.inputs[0].default_value = 1.0
        nt.links.new(blur.outputs["Image"], inv.node.inputs[1])
        fac = _math(nt, 'MULTIPLY', inv, value=max(a, 0.0), x=x + 540, y=y - 250)
        black = nt.nodes.new("CompositorNodeRGB")
        black.location = (x + 360, y - 120)
        black.outputs[0].default_value = (0, 0, 0, 1)
        return _mix(nt, img, black.outputs[0], fac, x=x + 720, y=y)
    else:
        return img
    n.location = (x, y)
    nt.links.new(img, n.inputs["Image"])
    return n.outputs["Image"]


_curves_store = {}


def _remember_curve(layer, node):
    pts = []
    for c in node.mapping.curves:
        pts.append([(p.location[0], p.location[1]) for p in c.points])
    _curves_store[layer.as_pointer()] = pts


def _restore_curve(layer, node):
    pts = _curves_store.get(layer.as_pointer())
    if pts is None:
        if layer.get("curve_points"):
            pts = layer["curve_points"]
        else:
            # gentle S-curve on the combined channel
            c = node.mapping.curves[3]
            c.points.new(0.25, 0.2)
            c.points.new(0.75, 0.8)
            node.mapping.update()
            return
    for curve, cpts in zip(node.mapping.curves, pts):
        while len(curve.points) > len(cpts):
            curve.points.remove(curve.points[-1])
        while len(curve.points) < len(cpts):
            curve.points.new(0.5, 0.5)
        for p, (px, py) in zip(curve.points, cpts):
            p.location = (px, py)
    node.mapping.update()


def build_group(scene):
    """(Re)build the compositor group from the layer stack and hook it to the scene."""
    post = scene.blendering_post
    nt = bpy.data.node_groups.get(GROUP_NAME)
    if nt is None:
        nt = bpy.data.node_groups.new(GROUP_NAME, "CompositorNodeTree")
    # keep curve edits across rebuilds
    for layer in post.layers:
        if layer.kind == 'CURVES' and layer.curve_node and layer.curve_node in nt.nodes:
            _remember_curve(layer, nt.nodes[layer.curve_node])
            layer["curve_points"] = _curves_store[layer.as_pointer()]
    nt.nodes.clear()
    if not any(i.in_out == 'OUTPUT' for i in nt.interface.items_tree):
        nt.interface.new_socket("Image", in_out='OUTPUT', socket_type='NodeSocketColor')

    rl = nt.nodes.new("CompositorNodeRLayers")
    rl.location = (-600, 0)
    rl.scene = scene
    out = nt.nodes.new("NodeGroupOutput")
    viewer = nt.nodes.new("CompositorNodeViewer")

    img = rl.outputs["Image"]
    product = None
    index_out = rl.outputs.get("Object Index") or rl.outputs.get("IndexOB")
    if any(l.mask != 'ALL' for l in post.layers) and index_out is not None:
        idm = nt.nodes.new("CompositorNodeIDMask")
        idm.location = (-350, -300)
        idm.inputs["Index"].default_value = PRODUCT_INDEX
        idm.inputs["Anti-Alias"].default_value = True
        nt.links.new(index_out, idm.inputs["ID value"])
        product = idm.outputs[0]

    x = -200
    if post.enabled:
        for layer in post.layers:
            if not layer.enabled:
                continue
            fx = _effect(nt, layer, img, x, 0)
            if layer.kind == 'CURVES':
                layer.curve_node = fx.node.name
            fac = None
            if layer.mask == 'PRODUCT' and product is not None:
                fac = product
            elif layer.mask == 'BACKGROUND' and product is not None:
                inv = _math(nt, 'SUBTRACT', product, value=0.0, x=x, y=-450)
                inv.node.inputs[0].default_value = 1.0
                nt.links.new(product, inv.node.inputs[1])
                fac = inv
            if fac is not None:
                fac = _math(nt, 'MULTIPLY', fac, value=layer.opacity, x=x + 150, y=-450)
                img = _mix(nt, img, fx, fac, x=x + 300, y=0)
            elif layer.opacity < 1.0:
                img = _mix(nt, img, fx, factor=layer.opacity, x=x + 300, y=0)
            else:
                img = fx
            x += 900 if layer.kind == 'VIGNETTE' else 500

    out.location = (x + 200, 0)
    viewer.location = (x + 200, -200)
    nt.links.new(img, out.inputs[0])
    nt.links.new(img, viewer.inputs[0])
    scene.compositing_node_group = nt
    scene.render.use_compositing = True
    return nt


def curve_node_for(scene, layer):
    nt = bpy.data.node_groups.get(GROUP_NAME)
    if nt and layer.curve_node in nt.nodes:
        return nt.nodes[layer.curve_node]
    return None
