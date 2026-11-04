# Blendering

A visualization toolkit for Blender, built for product, furniture and interior designers who model in Rhino and create their images in Blender.

> Status: early development (v0.1.0). Nothing here is ready for production work yet.

## What it will do

1. **Rhino bridge** – Rhino saves a `.3dm` file to a watched folder, Blendering updates the Blender scene. Materials, lights and cameras you set up in Blender are kept, because every object is matched by its Rhino ID.
2. **Studio builder** – describe a studio in words or drop in a reference photo, and Blendering builds a real 3D studio: backdrop, softboxes, HDRI, shadow catcher and camera.
3. **Scene-aware AI** – the AI receives depth, normal and object masks from the scene, so it knows where things are. Three modes per camera:
   - *Concept*: the AI paints the whole image from the scene passes.
   - *Hybrid*: a quick Blender draft render, refined by the AI.
   - *Exact*: a full Cycles render; the AI only adds surroundings.
   With *product protection* on, the product pixels always come from the render, so details, labels and colours stay exact.
4. **Post-processing** – non-destructive layers, masks and adjustments that re-apply automatically after every new render.

## Requirements

- Blender 5.2 or newer
- Rhino (any version that saves `.3dm`) for modelling
- An API key for a cloud AI service (for the AI layer)

## Rhino bridge (v0.1)

Open the **Blendering** tab in the 3D viewport sidebar (N), pick the `.3dm` file and press **Sync from Rhino**. Turn on **Auto-sync on save** to update the scene every time Rhino saves the file.

- Rhino layers become collections under `Rhino · <file name>`; objects are named after their layer.
- Blocks become collection instances; their definitions live in the excluded `Blocks · <file name>` collection.
- Units are converted to metres and the scene shows millimetres.
- Materials you assign in Blender are kept on re-sync. A new object gets the Blender material whose name matches its Rhino layer, if one exists.
- Objects deleted in Rhino are removed from Blender.

The bridge needs the `rhino3dm` library in Blender's Python. Until it is bundled with the extension, install it from Blender's Python console:

```python
import sys, subprocess, bpy
target = bpy.utils.user_resource('SCRIPTS', path="modules", create=True)
subprocess.run([sys.executable, "-m", "pip", "install", "--target", target, "rhino3dm"])
```

## Installing (development)

Build the extension from the `addon` folder:

```
blender --command extension build --source-dir addon
```

Then install the generated `.zip` from *Edit → Preferences → Get Extensions → Install from Disk*.

## Roadmap

| Phase | Content | Done when |
| --- | --- | --- |
| 1 | File-based Rhino bridge, base presets | A real project updates without losing materials |
| 2 | AI layer | An AI element can be regenerated after a camera change |
| 3 | Post-processing panel | A finished image without Photoshop |
| 4 | Live Rhino connection | Changes arrive without saving |

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
