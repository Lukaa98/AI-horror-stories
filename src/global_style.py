# global_style.py

"""
This module defines the global aesthetic style used across all
generated videos to ensure consistency for your YouTube Shorts channel.

Any scene prompt will automatically have this style appended
before being passed to Gemini Veo.

This ensures:
- consistent color palette,
- consistent horror tone,
- consistent camera motion,
- consistent brand identity.
"""

GLOBAL_VIDEO_STYLE = (
    "Photorealistic wildlife documentary filmed in the style of National Geographic. "
    "Soft natural lighting, shallow depth of field, smooth cinematic motion, "
    "gentle camera pans, warm sunlight tones, subtle film grain. "
    "The creature should look like a realistic animal adaptation of a Pokémon, "
    "with natural textures, believable anatomy, and expressive eyes. "
    "Natural environment matching its habitat. "
    "Realistic fur, scales, feathers, or skin depending on species. "
    "9:16 vertical smartphone composition with strong foreground depth."
)

