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
    "Cinematic wildlife documentary style inspired by National Geographic. "
    "The character is the clear main subject in every shot. "
    "Real-world natural habitat with grounded lighting and physical detail. "
    "Soft, warm sunlight with gentle blue ambient tones, natural shadows, "
    "and a shallow depth of field that keeps the character in sharp focus. "
    "Smooth slow camera pans and wildlife-style handheld realism. "
    "Highly recognizable character design: accurate colors, silhouette, face, and proportions. "
    "Detailed textures such as fur, scales, fabric, or skin depending on the character. "
    "Volumetric atmospheric fog and soft bokeh in the background. "
    "9:16 vertical smartphone composition with immersive cinematic framing."
)


