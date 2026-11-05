# SPDX-License-Identifier: GPL-3.0-or-later
"""Photo-scanned PBR materials from free CC0 libraries (Poly Haven, ambientCG).

Textures are downloaded once into the extension's user folder and reused.
Materials use box projection on object coordinates, so they work on Rhino
geometry that has no UV map, and they are scaled to real-world size.
"""

import json
import math
import os
import urllib.request
import zipfile

import bpy

UA = {"User-Agent": "Blendering-addon (https://github.com/vvsarolta/blendering)"}

# key: (source, asset id, texture width in metres (None = from the source),
#       tint sRGB 0-255 or None, saturation multiplier)
LIBRARY = {
    "Pine, raw (photo)": ("ambientcg", "Wood050", 0.8, None, 0.85),
    "Wool twill, charcoal (photo)": ("polyhaven", "poly_wool_herringbone", None, (44, 43, 42), 0.5),
}


def _cache_dir():
    try:
        return bpy.utils.extension_path_user(__package__, path="textures", create=True)
    except Exception:
        return bpy.utils.user_resource('DATAFILES', path=os.path.join("blendering", "textures"), create=True)


def _get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60).read()


def _download(url, path):
    if not os.path.exists(path):
        data = _get(url)
        with open(path, "wb") as fh:
            fh.write(data)
    return path


def fetch_polyhaven(asset, res="2k"):
    folder = os.path.join(_cache_dir(), "polyhaven", asset)
    os.makedirs(folder, exist_ok=True)
    files = json.loads(_get(f"https://api.polyhaven.com/files/{asset}"))
    info = json.loads(_get(f"https://api.polyhaven.com/info/{asset}"))
    maps = {}
    for role, keys in {"color": ["Diffuse", "diff"], "rough": ["Rough", "rough"],
                       "height": ["Displacement", "disp"]}.items():
        for k in keys:
            if k in files:
                entry = files[k][res]
                fmt = "jpg" if "jpg" in entry else next(iter(entry))
                url = entry[fmt]["url"]
                maps[role] = _download(url, os.path.join(folder, os.path.basename(url)))
                break
    dims = info.get("dimensions")  # millimetres [w, h]
    width_m = dims[0] / 1000.0 if dims else 1.0
    return maps, width_m


def fetch_ambientcg(asset, res="2K"):
    folder = os.path.join(_cache_dir(), "ambientcg", asset)
    os.makedirs(folder, exist_ok=True)
    if not any(f.endswith(".jpg") for f in os.listdir(folder)):
        zpath = _download(f"https://ambientcg.com/get?file={asset}_{res}-JPG.zip",
                          os.path.join(folder, f"{asset}.zip"))
        with zipfile.ZipFile(zpath) as z:
            z.extractall(folder)
        os.remove(zpath)
    maps = {}
    for f in os.listdir(folder):
        low = f.lower()
        if low.endswith("_color.jpg"):
            maps["color"] = os.path.join(folder, f)
        elif low.endswith("_roughness.jpg"):
            maps["rough"] = os.path.join(folder, f)
        elif low.endswith("_displacement.jpg"):
            maps["height"] = os.path.join(folder, f)
    return maps, 1.0


def _img(nt, path, colorspace, x, y, vec):
    node = nt.nodes.new("ShaderNodeTexImage")
    node.location = (x, y)
    node.image = bpy.data.images.load(path, check_existing=True)
    node.image.colorspace_settings.name = colorspace
    node.projection = 'BOX'
    node.projection_blend = 0.15
    nt.links.new(vec, node.inputs["Vector"])
    return node


def build(name, maps, width_m, tint=None, rotation=(0.0, 0.0, 0.0), saturation=1.0):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (700, 0)
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (400, 0)
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

    tc = nt.nodes.new("ShaderNodeTexCoord")
    tc.location = (-900, 0)
    mp = nt.nodes.new("ShaderNodeMapping")
    mp.location = (-700, 0)
    s = 1.0 / width_m
    mp.inputs["Scale"].default_value = (s, s, s)
    mp.inputs["Rotation"].default_value = rotation
    nt.links.new(tc.outputs["Object"], mp.inputs["Vector"])
    vec = mp.outputs["Vector"]

    if "color" in maps:
        col = _img(nt, maps["color"], "sRGB", -400, 250, vec)
        color_out = col.outputs["Color"]
        if saturation != 1.0:
            hs = nt.nodes.new("ShaderNodeHueSaturation")
            hs.location = (-100, 300)
            hs.inputs["Saturation"].default_value = saturation
            hs.inputs["Hue"].default_value = 0.51  # nudge pinkish scans towards yellow
            nt.links.new(color_out, hs.inputs["Color"])
            color_out = hs.outputs["Color"]
        if tint is not None:
            mix = nt.nodes.new("ShaderNodeMix")
            mix.data_type = 'RGBA'
            mix.blend_type = 'MULTIPLY'
            mix.location = (100, 300)
            mix.inputs["Factor"].default_value = 1.0
            from .materials import _srgb
            # tint is the target average colour; scale so a mid-grey texture lands on it
            mix.inputs["B"].default_value = tuple(min(1.0, c * 2.2) for c in _srgb(*tint)[:3]) + (1.0,)
            nt.links.new(color_out, mix.inputs["A"])
            color_out = mix.outputs["Result"]
        nt.links.new(color_out, bsdf.inputs["Base Color"])
    if "rough" in maps:
        r = _img(nt, maps["rough"], "Non-Color", -400, -50, vec)
        nt.links.new(r.outputs["Color"], bsdf.inputs["Roughness"])
    if "height" in maps:
        h = _img(nt, maps["height"], "Non-Color", -400, -350, vec)
        bump = nt.nodes.new("ShaderNodeBump")
        bump.location = (100, -300)
        bump.inputs["Strength"].default_value = 0.35
        bump.inputs["Distance"].default_value = 0.002
        nt.links.new(h.outputs["Color"], bump.inputs["Height"])
        nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    return mat


def _rotation_for(grain, thin):
    """Image grain runs along U. Rotate object space so U follows ``grain`` on the board's big faces."""
    if grain == 'Z':
        return (math.radians(90), 0.0, 0.0) if thin == 'X' else (0.0, math.radians(90), 0.0)
    if grain == 'Y':
        return (0.0, 0.0, math.radians(90))
    return (0.0, 0.0, 0.0)


def get(key, grain='X', thin='Z'):
    """Material ``key`` from LIBRARY, oriented for a board with the given grain and thin axis."""
    source, asset, width_m, tint, saturation = LIBRARY[key]
    name = f"BL · {key}" + (f" · {grain}/{thin}" if (grain, thin) != ('X', 'Z') else "")
    mat = bpy.data.materials.get(name)
    if mat is not None:
        return mat
    maps, src_width = (fetch_polyhaven(asset) if source == "polyhaven" else fetch_ambientcg(asset))
    return build(name, maps, width_m or src_width, tint, _rotation_for(grain, thin), saturation)


def assign_wood(objects, key="Pine, raw (photo)"):
    from .materials import wood_axis
    for obj in objects:
        if obj.type != 'MESH':
            continue
        d = obj.dimensions
        thin = 'XYZ'[min(range(3), key=lambda i: d[i])]
        mat = get(key, wood_axis(obj), thin)
        obj.data.materials.clear()
        obj.data.materials.append(mat)
