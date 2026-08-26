# Symbol index

Generated — do not edit by hand. Refresh with:

```bash
./venv/bin/python scripts/gen_index.py
```

Public top-level classes, methods and functions only; anything named with a leading `_` is internal to its module. Line numbers are a starting point, not a promise.

## The app (`src/`)

### `src/animator_common.py` — 83 lines
Script Animator — the constants and the one Qt helper its modules share.

`fit_scroll_content`:53

### `src/animator_page.py` — 946 lines
Script Animator page: a structured ad script (hook variations, body, CTA variants) -> duration-slotted scene prompts.

`AnimatorPage`:57 · `AnimatorPage.language_name`:444 · `AnimatorPage.tail`:447 · `AnimatorPage.pronunciation`:450

### `src/animator_panel.py` — 327 lines
Script Animator - the always-visible floating step-through panel.

`AnimatorFloatPanel`:26 · `AnimatorFloatPanel.update_scenes`:215 · `AnimatorFloatPanel.set_index`:221

### `src/animator_pipeline.py` — 653 lines
Script Animator - the Gemini passes and the session log.

`ScenePipelineWorker`:366 · `ScenePipelineWorker.run`:403 · `log_save`:629 · `log_load`:642

### `src/animator_widgets.py` — 352 lines
Script Animator - the row and card widgets of the two stages.

`BlockRow`:28 · `BlockRow.value`:82 · `BlockRow.set_value`:85 · `BlockRow.set_tag`:88 · `BlockRow.tag`:91 · `BlockRow.set_removable`:94 · `BlockRow.set_last`:97 · `FillMeter`:133 · `SceneCard`:177 · `SceneCard.refresh_prompt`:335 · `SceneCard.set_expanded`:339 · `SceneCard.set_selected`:343

### `src/camera_page.py` — 646 lines
Camera Prompts page: a searchable gallery of shot/angle references that composes a Gemini prompt.

`GeminiWorker`:68 · `GeminiWorker.run`:80 · `CameraPromptsPage`:90

### `src/camera_widgets.py` — 301 lines
Camera Prompts - the gallery widgets.

`RoundedImage`:57 · `PromptCard`:90 · `PromptCard.set_selected`:145 · `FlowLayout`:160 · `FlowLayout.count`:172 · `CategorySection`:235 · `CategorySection.add_card`:271 · `CategorySection.reflow`:274

### `src/caption_compare.py` — 515 lines
ComparePanel — EXPERIMENTAL caption QA overlay (approach B).

`ComparePanel`:55 · `ComparePanel.set_srt`:178

### `src/captions_page.py` — 264 lines
Captions DE: WhisperX + Gemini -> .srt, run in the separate WhisperX venv.

`whisperx_arch_ok`:36 · `CaptionsPage`:56 · `CaptionsPage.build_form`:76 · `CaptionsPage.validate`:202 · `CaptionsPage.build_command`:214 · `CaptionsPage.after_finished`:222

### `src/core.py` — 299 lines
Shared foundation for Mariposa Studio: paths, the .env helpers, and the small platform/icon helpers used across every page module.

`studio_python`:79 · `make_qprocess_env`:85 · `chevron_icon`:103 · `arrow_icon`:110 · `reveal_in_finder`:115 · `open_folder`:133 · `make_nonactivating_panel`:150 · `ensure_windows_shortcut`:232 · `read_env_value`:276 · `write_env_value`:286

### `src/design.py` — 246 lines
Mariposa Studio — Design System (single source of truth).

`load_fonts`:31 · `svg_icon`:197 · `svg_pixmap`:202 · `app_accent`:207 · `primary_button_style`:213 · `brand_pixmap`:222

### `src/extract_frame_page.py` — 133 lines
Extract Frame: pull the last, first, random or every-N-seconds frame (OpenCV).

`ExtractFramePage`:23 · `ExtractFramePage.build_form`:40 · `ExtractFramePage.validate`:94 · `ExtractFramePage.build_command`:108 · `ExtractFramePage.after_finished`:119

### `src/flow_cropper_page.py` — 359 lines
Flow Cropper: batch 9:16 -> 4:5 crops via ffmpeg, named from the briefing.

`FlowCropperPage`:99 · `FlowCropperPage.build_form`:106 · `FlowCropperPage.extra_action_buttons`:188 · `FlowCropperPage.ad_format_value`:253 · `FlowCropperPage.validate`:280 · `FlowCropperPage.build_command`:298 · `FlowCropperPage.after_finished`:320

### `src/gemini.py` — 218 lines
Gemini over plain HTTPS — the one transport the app uses.

`ssl_context`:50 · `GeminiError`:107 · `generate_text`:170 · `generate_json`:189

### `src/launcher.py` — 464 lines
The OS-style shell pieces: Settings, the launcher desktop with app icons, and the Spotlight overlay.

`SettingsPage`:33 · `AppIcon`:180 · `AppIcon.event`:231 · `LauncherPage`:259 · `LauncherPage.focus_first`:350 · `SpotlightOverlay`:383 · `SpotlightOverlay.open`:418

### `src/make_icon.py` — 174 lines
Render AppIcon.icns for the Mariposa Studio .app bundle.

`draw_icon`:64 · `write_multi_ico`:113 · `main`:133

### `src/script_packer.py` — 827 lines
Scene logic for the Script Animator — pure logic, no Qt, no network.

`ceiling`:96 · `split_long_sentence`:238 · `performance_beats`:277 · `pause_between`:302 · `analytic_seconds`:324 · `timing_source`:348 · `estimate_seconds`:354 · `nearest_slot`:368 · `assign_duration`:384 · `pack_sentences`:484 · `collapse_to_one`:522 · `relabel`:531 · `merge_scenes`:555 · `split_scene`:576 · `set_duration`:593 · `overruns`:613 · `flag_for`:623 · `ends_mid_sentence`:684 · `finalise_block`:728 · `pack_block`:777 · `build_prompt`:801 · `build_markdown`:816 · `format_runtime`:825

### `src/script_text.py` — 634 lines
The language layer under the Animator: words, sentences and seams.

`count_syllables`:145 · `split_sentences`:152 · `word_forms`:180 · `in_vocabulary`:198 · `fragment_sentence`:362 · `infer_link`:386 · `openers_for`:473 · `numeral_re`:522 · `pronunciation_for`:550 · `parse_pronunciation`:560 · `apply_pronunciation`:577 · `leftover_symbols`:595 · `verbatim_gaps`:609

### `src/speech_clock.py` — 455 lines
How long a line takes to say — **measured**, not estimated.

`Engine`:78 · `Engine.path`:118 · `Engine.available`:131 · `Engine.voice_for`:134 · `Engine.command`:138 · `engine_named`:189 · `available_engine`:193 · `reset_engine_probe`:200 · `engine_note`:205 · `load_calibration`:258 · `calibration_for`:269 · `wav_speech_seconds`:294 · `flush_cache`:353 · `clear_cache`:368 · `measure_raw`:386 · `measure`:431 · `duration_of`:444

### `src/studio.py` — 197 lines
Mariposa Studio - one hub for the editing-pipeline tools.

`MainWindow`:44 · `main`:172

### `src/stylesheet.py` — 668 lines
The app-wide QSS, built from the tokens in `design`.

`build_stylesheet`:29

### `src/tool_page.py` — 386 lines
`ToolPage` - the base every subprocess-backed tool page is built on.

`ToolPage`:34 · `ToolPage.build_form`:170 · `ToolPage.build_command`:173 · `ToolPage.validate`:176 · `ToolPage.after_finished`:179 · `ToolPage.extra_action_buttons`:182 · `ToolPage.add_row`:187 · `ToolPage.add_widget`:193 · `ToolPage.settings_card`:197 · `ToolPage.group_label`:207 · `ToolPage.grid_2col`:213 · `ToolPage.divider`:234

### `src/updater.py` — 296 lines
In-app auto-update for Mariposa Studio (Strategy A: source overlay).

`current_version`:53 · `is_newer`:69 · `fetch_latest`:83 · `apply_update`:152 · `relaunch`:176 · `UpdateBanner`:210 · `UpdateBanner.present`:245 · `attach_updater`:289

### `src/widgets.py` — 620 lines
Reusable UI widgets for Mariposa Studio (cards, drop zones, controls, console view, app bar). Shared by every page.

`Card`:26 · `FormRow`:38 · `DropZone`:85 · `DropZone.value`:212 · `DropZone.set_value`:215 · `Segmented`:226 · `Field`:280 · `ChipGroup`:309 · `ChipGroup.set_presets`:328 · `Switch`:356 · `ConsoleView`:392 · `ConsoleView.append_line`:403 · `AppBar`:421 · `AppBar.add_right`:453 · `Select`:496

## Build & test scripts (`scripts/`)

### `scripts/fit_clock.py` — 315 lines
Fit the speech clock against clips confirmed in production.

`load_rows`:61 · `languages_of`:77 · `Row`:82 · `Row.seconds`:99 · `measure_row`:103 · `satisfied`:115 · `hits_at`:120 · `fit`:130 · `fit_and_print`:151 · `report`:179 · `adopt_or_keep`:211 · `main`:246

### `scripts/gen_index.py` — 133 lines
Regenerate docs/INDEX.md — every public symbol in the app, with file:line.

`first_line`:31 · `module_summary`:38 · `is_qt_override`:44 · `symbols`:53 · `build`:72 · `main`:116

### `scripts/make_release_zip.py` — 50 lines
Build the distributable release zip for Mariposa Studio.

`main`:25

### `scripts/smoketest.py` — 53 lines
Headless smoke test: construct and show MainWindow offscreen, then quit.

_No public symbols._

### `scripts/test_clock.py` — 209 lines
Offline checks for speech_clock — no Qt, no network, no API key.

`check`:33

### `scripts/test_packer.py` — 751 lines
Offline checks for script_packer — no Qt, no network, no API key.

`check`:50 · `sent`:58

### `scripts/upsert_env.py` — 50 lines
Upsert a KEY=VALUE into tools/captions-de/.env, preserving every other line.

`upsert`:23 · `main`:42

## Bundled tool scripts (`tools/`) — separate processes, not imported

### `tools/captions-de/caption.py` — 1947 lines
Generate TikTok-style captions (SRT) from a video file. German is the default; Polish, French, Italian (plus English and Spanish) are selected with --language a

`text_width`:80 · `auto_hyphenate`:200 · `apply_auto_hyphenation`:220 · `join_soft_hyphens`:240 · `flatten_lines`:258 · `drop_midline_hyphens`:278 · `strip_punct`:289 · `clean_for_output`:295 · `normalize_case`:327 · `insert_compound_hyphens`:341 · `tokenize_for_packing`:349 · `pack_lines`:365 · `format_caption`:414 · `fmt_time`:418 · `get_video_duration`:428 · `run_whisperx`:454 · `load_words`:512 · `build_generic_prompt`:614 · `segment_with_ai`:700 · `review_grouping`:772 · `segment_heuristic`:947 · `compute_boundaries`:978 · `fix_line_break`:1010 · `normalize_text_preserve_breaks`:1046 · `apply_canonical_terms`:1143 · `project_terms_block`:1190 · `finalize_caption`:1204 · `move_trailing_binders`:1390 · `split_emphasis_repeats`:1416 · `merge_orphans`:1458 · `merge_split_numbers`:1566 · `merge_short_durations`:1593 · `enforce_single_line`:1678 · `learn_and_relabel_case`:1731 · `recase_with_ai`:1809 · `write_srt`:1848 · `main`:1855

### `tools/captions-de/caption_qa.py` — 342 lines
PROTOTYPE — Approach B: Gemini-based caption QA pass.

`parse_srt`:45 · `qa_check`:171 · `main`:277

### `tools/captions-de/install.py` — 167 lines
Cross-platform installer for the caption tool.

`section`:23 · `check_python`:30 · `check_ffmpeg`:42 · `make_venv`:75 · `venv_python`:103 · `install_whisperx`:124 · `write_env_example`:133 · `main`:145

### `tools/extract-frame/extract_last_frame.py` — 91 lines
Extract frames from a video.

`extract`:24

### `tools/flow-cropper/crop.py` — 857 lines
Flow Cropper — 9:16 → 4:5 batch crop + smart rename.

`normalize_creator`:85 · `safe_print`:98 · `rename_with_retry`:115 · `find_ffmpeg`:151 · `detect_creative_id`:170 · `select_encoder`:213 · `crop_to_4x5`:259 · `normalize_creative_id`:272 · `creative_name`:281 · `simple_name`:294 · `process_folder`:323 · `detect_structure`:421 · `run`:462 · `undo_last`:507 · `pick_folder`:568 · `ask_text`:586 · `ask_choice`:609 · `alert`:652 · `interactive`:680 · `run_with`:723 · `main`:791
