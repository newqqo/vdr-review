# G.726 decoder notice

`scripts/vdr_review/src/g726.js` is a JavaScript port of FFmpeg
`libavcodec/g726.c` (release/7.1), Copyright (c) 2004 Roman Shaposhnik,
licensed under the GNU Lesser General Public License version 2.1 or later
(`LGPL-2.1.txt`). The port keeps the decoder arithmetic and int16 behaviour,
reads big-endian code words, and resets the decoder per input buffer (one VDR
minute file). Tests compare its output sample by sample with FFmpeg.

The built single HTML file embeds this source unminified together with this
licence text, so recipients receive the corresponding source.
