# SPDX-License-Identifier: GPL-3.0-or-later
"""Free CC0 3D models and HDRI environments from Poly Haven.

Real 3D props instead of AI-painted pixels: a plant placed next to the product is
the same plant from every camera, lit by the same lights, and it never touches
the product. Downloads are cached, so each asset is fetched only once.
"""

import json
import math
import os
import random
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import bpy
from mathutils import Vector

UA = {"User-Agent": "Blendering-addon (https://github.com/vvsarolta/blendering)"}
API = "https://api.polyhaven.com"
PROPS_COLL = "Blendering · Props"

_index = {}          # kind -> {id: info}
_previews = None     # bpy.utils.previews collection


# ------------------------------------------------------------------ network

def _get(url, timeout=60):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout).read()


def _json(url):
    return json.loads(_get(url))


def _cache(*parts):
    try:
        base = bpy.utils.extension_path_user(__package__, path="assets", create=True)
    except Exception:
        base = bpy.utils.user_resource('DATAFILES', path=os.path.join("blendering", "assets"), create=True)
    path = os.path.join(base, *parts)
    os.makedirs(path, exist_ok=True)
    return path


def _download(url, path):
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = _get(url, timeout=300)
        with open(path + ".part", "wb") as fh:
            fh.write(data)
        os.replace(path + ".part", path)
    return path


# ------------------------------------------------------------------- search

def index(kind):
    """All Poly Haven assets of ``kind`` ('models' or 'hdris'): {id: info}."""
    if kind not in _index:
        _index[kind] = _json(f"{API}/assets?t={kind}")
    return _index[kind]


def search(kind, text="", limit=36):
    """Asset ids matching every word of ``text`` in name, tags or categories, most popular first."""
    words = [w for w in text.lower().split() if w]
    hits = []
    for aid, info in index(kind).items():
        hay = " ".join([aid, info.get("name", "")] + info.get("tags", []) + info.get("categories", [])).lower()
        if all(w in hay for w in words):
            hits.append((info.get("download_count", 0), aid))
    hits.sort(reverse=True)
    return [aid for _, aid in hits[:limit]]


def previews():
    global _previews
    if _previews is None:
        import bpy.utils.previews
        _previews = bpy.utils.previews.new()
    return _previews


def load_thumbnails(ids):
    """Download (in parallel) and load the thumbnails of ``ids``; returns {id: icon_id}."""
    folder = _cache("thumbs")
    todo = [i for i in ids if not os.path.exists(os.path.join(folder, i + ".png"))]

    def fetch(aid):
        try:
            _download(f"https://cdn.polyhaven.com/asset_img/thumbs/{aid}.png?width=192&height=192",
                      os.path.join(folder, aid + ".png"))
        except Exception:
            pass

    if todo:
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(fetch, todo))
    pcoll = previews()
    icons = {}
    for aid in ids:
        path = os.path.join(folder, aid + ".png")
        if not os.path.exists(path):
            continue
        if aid not in pcoll:
            pcoll.load(aid, path, 'IMAGE')
        icons[aid] = pcoll[aid].icon_id
    return icons


# ------------------------------------------------------------------- models

def fetch_model(aid, res="1k"):
    """Download the .blend of model ``aid`` with its textures; returns the .blend path."""
    files = _json(f"{API}/files/{aid}")
    entry = files["blend"][res]["blend"]
    folder = _cache("models", aid, res)
    blend = _download(entry["url"], os.path.join(folder, os.path.basename(entry["url"])))
    includes = entry.get("include", {})

    def fetch(item):
        rel, info = item
        _download(info["url"], os.path.join(folder, rel))

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(fetch, includes.items()))
    return blend


def _props_collection(scene):
    coll = bpy.data.collections.get(PROPS_COLL)
    if coll is None:
        coll = bpy.data.collections.new(PROPS_COLL)
    if coll.name not in scene.collection.children:
        scene.collection.children.link(coll)
    return coll


def _surface_below(scene, point, ignore=()):
    """Highest surface under ``point`` (ray cast straight down), or None."""
    deps = bpy.context.evaluated_depsgraph_get()
    origin = point + Vector((0, 0, 2.0))
    for _ in range(8):
        hit, loc, normal, _i, obj, _m = scene.ray_cast(deps, origin, Vector((0, 0, -1)))
        if not hit:
            return None
        if obj is not None and obj.original in ignore:
            origin = loc - Vector((0, 0, 1e-4))
            continue
        return loc
    return None


def add_model(aid, scene=None, location=None, rotation_z=None, res="1k", on_surface=True):
    """Append model ``aid`` as one movable prop, standing on the surface at ``location``."""
    scene = scene or bpy.context.scene
    blend = fetch_model(aid, res)
    with bpy.data.libraries.load(blend, link=False) as (src, dst):
        dst.objects = list(src.objects)
    objs = [o for o in dst.objects if o is not None]
    coll = _props_collection(scene)
    name = index("models").get(aid, {}).get("name", aid)
    root = bpy.data.objects.new(name, None)
    root.empty_display_type = 'PLAIN_AXES'
    root.empty_display_size = 0.05
    root["blendering_prop"] = aid
    coll.objects.link(root)
    for o in objs:
        coll.objects.link(o)
        if o.parent is None:
            o.parent = root
    bpy.context.view_layer.update()

    # put the root at the bottom centre of the model, so it stands where it is placed
    pts = [o.matrix_world @ Vector(c) for o in objs if o.type == 'MESH' for c in o.bound_box]
    if pts:
        lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
        hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
        base = Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, lo.z))
        for o in objs:
            if o.parent is root:
                o.location -= base

    target = location.copy() if location is not None else scene.cursor.location.copy()
    if on_surface:
        hit = _surface_below(scene, target, ignore=set(objs) | {root})
        if hit is not None:
            target = hit
    root.location = target
    root.rotation_euler.z = random.uniform(0, 2 * math.pi) if rotation_z is None else rotation_z
    return root


# -------------------------------------------------------------------- HDRIs

def set_hdri(aid, scene=None, strength=1.0, rotation_deg=0.0, res="2k", visible=True):
    """Light the scene with HDRI ``aid`` and (optionally) show it as the background."""
    scene = scene or bpy.context.scene
    files = _json(f"{API}/files/{aid}")
    entry = files["hdri"][res]["hdr"]
    path = _download(entry["url"], os.path.join(_cache("hdris"), os.path.basename(entry["url"])))
    world = scene.world or bpy.data.worlds.new("World")
    scene.world = world
    world.use_nodes = True
    nt = world.node_tree
    nt.nodes.clear()
    tc = nt.nodes.new("ShaderNodeTexCoord"); tc.location = (-900, 0)
    mp = nt.nodes.new("ShaderNodeMapping"); mp.location = (-700, 0)
    mp.inputs["Rotation"].default_value = (0, 0, math.radians(rotation_deg))
    env = nt.nodes.new("ShaderNodeTexEnvironment"); env.location = (-450, 0)
    env.image = bpy.data.images.load(path, check_existing=True)
    bg = nt.nodes.new("ShaderNodeBackground"); bg.location = (-150, 0)
    bg.inputs["Strength"].default_value = strength
    out = nt.nodes.new("ShaderNodeOutputWorld"); out.location = (100, 0)
    nt.links.new(tc.outputs["Generated"], mp.inputs["Vector"])
    nt.links.new(mp.outputs["Vector"], env.inputs["Vector"])
    nt.links.new(env.outputs["Color"], bg.inputs["Color"])
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])
    scene.render.film_transparent = not visible
    world["blendering_hdri"] = aid
    return world


def unregister():
    global _previews
    if _previews is not None:
        import bpy.utils.previews
        bpy.utils.previews.remove(_previews)
        _previews = None
