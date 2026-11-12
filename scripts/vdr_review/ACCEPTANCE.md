# VDR acceptance / 實檔與公司電腦驗收

This is a blank acceptance checklist, **not a completed acceptance report**.
已實作格式、合成測試及機型／實機通過是三件不同的事。不得用這張空表宣稱驗收。
Do not put completed real-case records, filenames or transcript excerpts in Git.

## Model and format matrix

| Model | Current format evidence | MIC + VHF | Minute boundary / midnight | Independent clock / vendor playback | 8 GB end-to-end |
| --- | --- | --- | --- | --- | --- |
| JCY-1800 | Observed the same G.726 layout and offset-timed log as JCY-1850 on one export; one minute per channel decoded identically to FFmpeg; not a full model pass | Pending | Pending | Pending | Pending |
| JCY-1850 | Observed G.726 layout and decoder comparisons; not a full model pass | Pending | Pending | Pending | Pending |
| JCY-1900 | Observed `Internal_Storage` AAC-LC audio (each record repeats the previous record's last 128 ms of frames) and time-stamped `L_` log layout on one export; not a full model pass | Pending | Pending | Pending | Pending |
| JCY-1950 | AAC-LC route exists; firmware/export layout must be checked | Pending | Pending | Pending | Pending |

Record locally for each run: tool source/build SHA, browser/OS version, physical
RAM, export method, model/firmware when known, record/container format, channel
sample rates, source identity, actual coverage, vendor-player version and result.
Unknown stays unknown. A model name alone cannot select or validate a decoder.

## Minimum real-source checks

- [ ] Preserve originals and source hashes; do not alter/re-encode evidence to pass.
- [ ] Check both MIC and VHF at the beginning, middle and end of the source.
- [ ] Listen across adjacent minute boundaries; record gaps/overlaps separately.
- [ ] Check cross-midnight ordering and complete dates, not only time-of-day.
- [ ] Compare the same identifiable event across channels and the vendor player.
- [ ] Verify the independent clock basis; document offset and uncertainty.
- [ ] Test the actual export layout; mark unsupported layouts as unsupported.

## Company 8 GB end-to-end run

Use the actual colleague's 8 GB workstation and ordinary company policy/account.
Do not reduce a 4-hour source to a short source and call that a 4-hour pass.

1. Load a 4-hour source and retain the coverage/indexing receipt.
2. Seek beginning/middle/end, cross minute/midnight boundaries and switch all
   relevant tracks while playing; verify time and waveform synchronization.
3. Export an ordinary WAV segment, import its SRT with the corresponding time
   alignment file, edit, split, merge, delete and undo without losing text.
4. Merge another progress JSON while keeping current front/mid/later content.
5. Save to the actual case file. Close the browser, reopen the tool and original
   source, reopen that JSON and check text, speakers, times and reviewed status.
6. Export the investigation bundle. Verify WAV duration/sample count, source and
   output SHA values, SRT alignment and full case JSON.
7. Separately check a 90/120-second enhanced clip with different audio levels on
   either side of the 60-second boundary; inspect all processing segments.

Observe the total browser processes' memory, not just JavaScript heap or one
32 MiB decoded-record cache. Record peak memory, swap pressure, responsiveness,
source length/channels and any crash. The 32 MiB cache is **not** a browser RAM cap.
Actual continuous 4-hour listening, if required, is a separate duration test;
short seeks across a 4-hour timeline are not continuous 4-hour playback.

Record failures before retesting. A failed/cancelled save, permission restriction,
missing interval or unsupported AAC decoder must remain visible in the result.
Approval must name the tested model/layout/device/browser combination; do not
extend one passing combination to all four models or all company computers.
