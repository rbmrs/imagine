# Image Motion From Stills Plan

Last updated: 2026-03-11

Purpose:
- Capture how image-only video generation is commonly done online.
- Compare those approaches to the current `Imagine` pipeline.
- Define the next practical implementation path for image-led videos.

## Bottom Line

Yes, this is worth doing.

But the right first move is not full AI image-to-video.

The strongest near-term upgrade for `Imagine` is:
- a much better deterministic still-image motion system
- plus an `images-only` / `prefer-images` asset mode
- plus multiple images per scene instead of stretching one image across the whole scene

That gives a clear quality jump with low operational risk and no extra paid dependencies.

## What The Current Repo Already Does

Current state in `src/local_video_mvp/pipeline.py`:
- still images are already supported as stock candidates and can be selected for scenes
- image rendering already applies a simple `zoompan` motion effect
- the current effect is essentially a centered push-in with light color polish

What is missing:
- multiple motion presets
- subject-aware framing
- easing / direction variety
- multiple images per scene
- a dedicated `images-only` video mode
- any depth-based parallax or AI image animation layer

So the feature is not starting from zero. It already has a base path.

## How This Is Commonly Done Online

### 1. Ken Burns / pan-and-zoom presets

This is still the default method in mainstream editors.

Typical pattern:
- start frame and end frame are defined
- position and scale are animated over the image duration
- easing is added so motion does not feel robotic
- creators reuse motion presets like:
  - zoom in
  - zoom out
  - pan left
  - pan right
  - tilt up
  - tilt down
  - diagonal drift

This is the most common baseline because it is cheap, deterministic, and visually acceptable when done well.

### 2. Focus-aware pan and zoom

Better tools do not just animate the image center.

They try to keep the important region in frame:
- face-aware focus
- subject-aware focus
- start/end boxes placed around the subject
- automatic panning path patterns

This matters because centered zooms waste a lot of good images.

### 3. Multi-image slideshow rhythm

A lot of “image video” tools are not actually one-image animations.

They improve engagement by:
- using multiple images inside the same scene
- changing framing every few seconds
- adding crossfades or short directional transitions
- mixing close/wide crops from different source images

This is a major practical point for `Imagine`:
- image-led videos feel much better when each scene uses 2-4 images instead of 1 image for 10-20 seconds

### 4. 2.5D parallax / depth-based motion

The more advanced non-generative approach is:
- estimate scene depth from one image
- separate foreground/background planes
- move a virtual camera slightly
- fill disoccluded gaps

This creates much more convincing motion than plain pan/zoom, especially for:
- architecture
- landscapes
- portraits
- temple/street/city imagery

It is significantly better looking, but also much more complex.

### 5. AI image-to-video

This is a different category.

Online tools now also let users:
- upload one image
- describe desired movement
- choose motion speed / duration / model
- generate a short fully synthetic clip

This can look impressive, but it is:
- slower
- less deterministic
- more expensive
- riskier for consistency across a full long-form video

For `Imagine`, this should be future-only, not the first implementation step.

## What Online Tools Suggest We Should Copy

The strongest ideas to copy now:

1. Motion presets, not one universal zoom
- `gentle-push-in`
- `pull-out`
- `pan-left`
- `pan-right`
- `tilt-up`
- `tilt-down`
- `diagonal-drift`
- `push-in-left`
- `push-in-right`

2. Start/end framing boxes
- define motion by two rectangles, not one fixed center formula
- this makes the effect easier to reason about and debug

3. Subject-aware focus
- even lightweight face detection or saliency would help
- if no subject is detected, fall back to deterministic framing rules

4. Multi-image scenes
- image-led scenes should rotate through several stills
- this is likely the single biggest engagement upgrade

5. Duration-aware intensity
- short clips can move more aggressively
- longer clips need slower motion and/or multiple images

## Recommended Direction For Imagine

### Phase 1: Better deterministic image motion

Implement first.

Goal:
- make still-image scenes look intentionally animated, not like “one photo slowly zooming forever”

Changes:
- replace the single image `zoompan` pattern with a preset selector
- keep it deterministic by hashing scene id + clip name
- use different motion families for different scene lengths
- add easing-like behavior through better interpolation expressions
- optionally add very light rotation / crop drift / polish only where safe

Expected result:
- immediate visual improvement
- no new external services
- stable local rendering

### Phase 2: Image-led scene composition

Implement second.

Goal:
- support videos built primarily from stills

Changes:
- add asset modes:
  - `prefer-video`
  - `balanced`
  - `prefer-images`
  - `images-only`
- allow resolving multiple selected images per scene
- split one scene into several sub-clips internally
- add simple transitions between sub-clips

Expected result:
- `Imagine` can generate full videos from image pools, not only fallback still shots

### Phase 3: Focus-aware image motion

Implement after Phase 1 is stable.

Goal:
- move around the subject, not the frame center

Possible methods:
- face detection
- saliency detection
- CLIP/object detection later

Expected result:
- stronger framing
- fewer awkward crops on people, food, streets, and landmarks

### Phase 4: Parallax-lite

Implement only after the image-led mode works.

Goal:
- create more convincing depth from premium hero images

Possible path:
- optional depth map generation
- foreground/background separation
- constrained virtual camera movement
- apply only to selected scenes, not every scene

Expected result:
- stronger “movement illusion” for important scenes
- higher complexity and higher failure risk

### Phase 5: AI image-to-video

Future-only.

This should be:
- explicit opt-in
- cost-aware
- used for hero moments or missing footage
- never the default base layer for long-form output

## Concrete Product Plan For This Repo

### New settings

Add:
- `asset_mode`
- `image_motion_style`
- `images_per_scene_min`
- `images_per_scene_max`
- `enable_focus_detection`
- `enable_depth_parallax` later

### New TUI controls

Add under asset strategy / asset policy:
- `Asset mode: prefer video / balanced / prefer images / images only`
- `Image motion style: subtle / documentary / dynamic`
- `Images per scene`

### New review data

Instead of one selected asset per image-led scene, support:
- one selected primary asset for video scenes
- multiple selected stills for image-led scenes

This likely needs a scene-level list such as:
- `selected_assets`

### New render behavior

For image-led scenes:
- split scene duration across multiple stills
- apply varied motion preset per still
- add short transitions between stills
- preserve deterministic output

## Recommended First Implementation Slice

Do this next:

1. Add `asset_mode` with `prefer-video`, `balanced`, `prefer-images`, `images-only`
2. Add 6-8 deterministic image motion presets
3. Add multi-image-per-scene support for image-led scenes
4. Add short crossfade transitions between stills
5. Expose image-led controls in the TUI

This is the highest-value next move.

## Why This Order Is Better

If we jump straight to AI image-to-video:
- quality will be inconsistent
- costs will appear quickly
- failures will be harder to debug
- render behavior becomes less deterministic

If we improve deterministic still-motion first:
- quality improves immediately
- image providers become much more valuable
- the pipeline stays local-first
- future AI motion can be added on top instead of replacing the base system

## Source Links

- [Adobe Premiere Pro: Apply a Ken Burns effect](https://helpx.adobe.com/ro/premiere-pro/how-to/ken-burns-effect.html)
- [Adobe Premiere Rush: Pan and zoom effects on still images](https://helpx.adobe.com/si/premiere-rush/help/effects-panel.html)
- [Adobe Premiere Elements: Pan and Zoom tool](https://helpx.adobe.com/premiere-elements/using/pan-zoom-create-video-like.html)
- [Adobe Research: 3D Ken Burns Effect from a Single Image](https://research.adobe.com/publication/3d-ken-burns-effect-from-a-single-image/)
- [Adobe Blog: Project #MovingStills](https://blog.adobe.com/en/publish/2018/11/21/project-movingstills-turns-still-photos-into-video)
- [Shotstack community: Ken Burns effect](https://community.shotstack.io/t/ken-burns-effect/136)
- [Shotstack docs: AI video generation / image to video](https://shotstack.io/docs/guide/generating-assets/ai-video-generation/)
- [CapCut: AI image to video](https://www.capcut.com/tools/ai-image-to-video)
