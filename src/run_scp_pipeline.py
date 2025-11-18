import os
from scp_generator import get_next_scp_number, generate_short_scp, save_entry
from generate_scp_video_gemini import make_videos_from_story

print("🚀 Starting SCP Pipeline...\n")

# 1. Generate SCP story
scp_num = get_next_scp_number()
entry = generate_short_scp(scp_num)
save_entry(entry)

print("\n📜 SCP ENTRY:\n")
print(entry["entry_text"])
print("\n---------------------------------\n")

# 2. Generate videos with Gemini Veo
story_path = os.path.join("stories", f"SCP-{scp_num:03d}.json")
final_video = make_videos_from_story(story_path)

print(f"\n🎉 DONE! Final video generated:\n{final_video}")
