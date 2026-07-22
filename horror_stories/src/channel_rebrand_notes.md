# Channel Rebrand Notes

## New Direction
- Channel focus: AI-powered mystery shorts and branching story episodes
- Flag clearly that the channel is AI-assisted or AI-generated
- Main flagship series: `Last Player Leo`

## Branding Direction
- Visual identity: digital ruins, ghost server, blue/cyan screen glow, orange headset light
- Tone: eerie, mysterious, emotional, internet-native
- Promise: audience can influence what happens next

## Channel Description Draft
`AI-powered mystery shorts where the audience helps choose what happens next. Follow Last Player Leo as he explores dead servers, ghost lobbies, and digital worlds that were never meant to stay alive. Vote in the comments to steer the next episode.`

## Upload CTA
`Vote in the comments to decide Leo's next move.`

## YouTube API Notes
- Can update channel branding settings like description and keywords with `channels.update`
- Can upload a channel banner with `channelBanners.insert` and then apply it with `channels.update`
- Can bulk change old Fortnite videos to `unlisted` or `private` with `videos.update`
- Do not assume profile photo/avatar can be updated through the same clean API flow
