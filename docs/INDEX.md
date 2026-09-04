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

### `src/animator_page.py` — 1025 lines
Script Animator page: a structured ad script (hook variations, body, CTA variants) -> duration-slotted scene prompts.

`AnimatorPage`:117 · `AnimatorPage.language_name`:531 · `AnimatorPage.tail`:534 · `AnimatorPage.pronunciation`:537

### `src/animator_panel.py` — 450 lines
Script Animator - the always-visible floating step-through panel.

`AnimatorFloatPanel`:68 · `AnimatorFloatPanel.update_scenes`:315 · `AnimatorFloatPanel.set_index`:321 · `AnimatorFloatPanel.set_generated`:399

### `src/animator_pipeline.py` — 653 lines
Script Animator - the Gemini passes and the session log.

`ScenePipelineWorker`:366 · `ScenePipelineWorker.run`:403 · `log_save`:629 · `log_load`:642

### `src/animator_scenes.py` — 416 lines
Script Animator, stage two: the cut.

`ScenesStage`:37 · `ScenesStage.open_float_panel`:389

### `src/animator_widgets.py` — 474 lines
Script Animator - the row and card widgets of the two stages.

`BlockRow`:28 · `BlockRow.value`:99 · `BlockRow.set_value`:102 · `BlockRow.set_tag`:105 · `BlockRow.tag`:108 · `BlockRow.set_timing`:111 · `BlockRow.set_removable`:130 · `BlockRow.set_last`:133 · `FillMeter`:188 · `SceneCard`:236 · `SceneCard.set_generated`:382 · `SceneCard.offer_split`:386 · `SceneCard.refresh_prompt`:457 · `SceneCard.set_expanded`:461 · `SceneCard.set_selected`:465

### `src/camera_page.py` — 733 lines
Camera Prompts page: a searchable gallery of shot/angle references that composes a Gemini prompt.

`GeminiWorker`:69 · `GeminiWorker.run`:80 · `CameraPromptsPage`:151

### `src/camera_widgets.py` — 314 lines
Camera Prompts - the gallery widgets.

`RoundedImage`:57 · `PromptCard`:90 · `PromptCard.set_selected`:147 · `FlowLayout`:173 · `FlowLayout.count`:185 · `CategorySection`:248 · `CategorySection.add_card`:284 · `CategorySection.reflow`:287

### `src/caption_compare.py` — 516 lines
ComparePanel — EXPERIMENTAL caption QA overlay (approach B).

`ComparePanel`:55 · `ComparePanel.set_srt`:179

### `src/captions_page.py` — 479 lines
Captions DE: WhisperX + Gemini -> .srt, run in the separate WhisperX venv.

`whisperx_arch_ok`:37 · `CaptionsPage`:83 · `CaptionsPage.build_form`:122 · `CaptionsPage.validate`:305 · `CaptionsPage.build_command`:323 · `CaptionsPage.advance_batch`:346 · `CaptionsPage.env_lines`:355 · `CaptionsPage.can_fix`:372 · `CaptionsPage.apply_fix`:375 · `CaptionsPage.after_finished`:393 · `CaptionsPage.progress_from_line`:445

### `src/clip_cutter_page.py` — 1109 lines
Clip Cutter: assemble a UGC creative from a clip folder and hand it to CapCut.

`hook_number`:185 · `ClipCutterPage`:227 · `ClipCutterPage.build_form`:326 · `ClipCutterPage.validate`:943 · `ClipCutterPage.build_command`:973 · `ClipCutterPage.after_finished`:1016 · `ClipCutterPage.can_fix`:1051 · `ClipCutterPage.apply_fix`:1100

### `src/clip_cutter_widgets.py` — 536 lines
Clip Cutter's drag-and-drop assembly widgets.

`video_urls`:39 · `register_thumb`:64 · `ClipChip`:102 · `PoolCard`:162 · `DropArea`:180 · `DropArea.names`:223 · `DropArea.set_names`:226 · `DropArea.add_name`:230 · `DropArea.remove_name`:236 · `BodyStrip`:376 · `SlotRow`:405 · `SlotRow.set_code`:466 · `SlotRow.names`:470 · `SlotRow.headline_text`:473 · `DropCue`:477 · `DashedButton`:530

### `src/core.py` — 373 lines
Shared foundation for Mariposa Studio: paths, the .env helpers, and the small platform/icon helpers used across every page module.

`studio_python`:114 · `make_qprocess_env`:120 · `chevron_icon`:138 · `arrow_icon`:145 · `reveal_in_finder`:150 · `notify`:168 · `open_folder`:196 · `make_nonactivating_panel`:213 · `ensure_windows_shortcut`:295 · `read_env_value`:339 · `gemini_model_override`:349 · `write_env_value`:360

### `src/design.py` — 342 lines
Mariposa Studio — Design System (single source of truth).

`load_fonts`:46 · `tint`:68 · `apply_shadow`:255 · `svg_icon`:293 · `svg_pixmap`:298 · `app_accent`:303 · `primary_button_style`:309 · `brand_pixmap`:318

### `src/diagnostics.py` — 414 lines
One error report, complete enough to fix a bug from — and safe to paste.

`redact`:83 · `note_log`:101 · `note_error`:108 · `last_error`:120 · `report`:194 · `save_report`:266 · `start_log`:351 · `install_hooks`:376

### `src/extract_frame_page.py` — 367 lines
Extract Frame: pull the last, first, random or every-N-seconds frame (OpenCV).

`BatchCard`:46 · `ExtractFramePage`:148 · `ExtractFramePage.build_form`:182 · `ExtractFramePage.validate`:325 · `ExtractFramePage.build_command`:339 · `ExtractFramePage.after_finished`:350

### `src/failures.py` — 211 lines
Turning a stack trace into a sentence and a button.

`Failure`:26 · `classify`:173 · `last_error_line`:183 · `describe`:200

### `src/first_run.py` — 334 lines
First run — one thing to paste in, and a look at what installs itself.

`should_show`:43 · `mark_done`:54 · `run_installer`:93 · `run_whisperx_installer`:105 · `FirstRunPage`:148

### `src/flow_cropper_page.py` — 519 lines
Flow Cropper: batch 9:16 -> 4:5 crops via ffmpeg, named from the briefing.

`FlowCropperPage`:153 · `FlowCropperPage.build_form`:164 · `FlowCropperPage.extra_action_buttons`:307 · `FlowCropperPage.ad_format_value`:363 · `FlowCropperPage.validate`:390 · `FlowCropperPage.on_output_line`:414 · `FlowCropperPage.build_command`:421 · `FlowCropperPage.after_finished`:443 · `FlowCropperPage.can_fix`:484 · `FlowCropperPage.apply_fix`:487

### `src/gemini.py` — 311 lines
Gemini over plain HTTPS — the one transport the app uses.

`ssl_context`:67 · `GeminiError`:124 · `models_to_try`:143 · `generate_text`:263 · `generate_json`:282

### `src/launcher.py` — 514 lines
The shell: the home grid of tools and the ⌘K overlay.

`AppIcon`:90 · `AppIcon.event`:150 · `LauncherPage`:177 · `LauncherPage.focus_first`:259 · `SpotlightOverlay`:333 · `SpotlightOverlay.open`:459

### `src/make_icon.py` — 176 lines
Render AppIcon.icns for the Mariposa Studio .app bundle.

`draw_icon`:66 · `write_multi_ico`:115 · `main`:135

### `src/script_packer.py` — 850 lines
Scene logic for the Script Animator — pure logic, no Qt, no network.

`ceiling`:96 · `split_long_sentence`:238 · `performance_beats`:277 · `pause_between`:302 · `analytic_seconds`:324 · `timing_source`:348 · `estimate_seconds`:354 · `nearest_slot`:368 · `assign_duration`:384 · `pack_sentences`:484 · `collapse_to_one`:522 · `relabel`:531 · `merge_scenes`:555 · `split_scene`:576 · `best_seam`:593 · `set_duration`:616 · `overruns`:636 · `flag_for`:646 · `ends_mid_sentence`:707 · `finalise_block`:751 · `pack_block`:800 · `build_prompt`:824 · `build_markdown`:839 · `format_runtime`:848

### `src/script_text.py` — 634 lines
The language layer under the Animator: words, sentences and seams.

`count_syllables`:145 · `split_sentences`:152 · `word_forms`:180 · `in_vocabulary`:198 · `fragment_sentence`:362 · `infer_link`:386 · `openers_for`:473 · `numeral_re`:522 · `pronunciation_for`:550 · `parse_pronunciation`:560 · `apply_pronunciation`:577 · `leftover_symbols`:595 · `verbatim_gaps`:609

### `src/session.py` — 97 lines
What this launch has made — held in memory, and only in memory.

`Artefact`:29 · `Artefact.is_dir`:37 · `record`:49 · `items`:59 · `clear`:67 · `note_gemini`:74 · `gemini_note`:79 · `ago`:87

### `src/settings_page.py` — 532 lines
Settings — still one field, because there is still only one thing to set.

`pref`:48 · `set_pref`:55 · `notify_if_enabled`:59 · `folder_size`:72 · `human_size`:93 · `stale_entries`:100 · `SettingsPage`:118

### `src/speech_clock.py` — 455 lines
How long a line takes to say — **measured**, not estimated.

`Engine`:78 · `Engine.path`:118 · `Engine.available`:131 · `Engine.voice_for`:134 · `Engine.command`:138 · `engine_named`:189 · `available_engine`:193 · `reset_engine_probe`:200 · `engine_note`:205 · `load_calibration`:258 · `calibration_for`:269 · `wav_speech_seconds`:294 · `flush_cache`:353 · `clear_cache`:368 · `measure_raw`:386 · `measure`:431 · `duration_of`:444

### `src/studio.py` — 294 lines
Mariposa Studio - one hub for the editing-pipeline tools.

`MainWindow`:73 · `main`:263

### `src/stylesheet.py` — 1076 lines
The app-wide QSS, built from the tokens in `design`.

`build_stylesheet`:34 · `font_pairs`:1015 · `font_health`:1038 · `font_problems`:1072

### `src/tool_page.py` — 597 lines
`ToolPage` — the base every subprocess-backed tool page is built on.

`ToolPage`:86 · `ToolPage.build_side`:212 · `ToolPage.env_lines`:216 · `ToolPage.set_env_lines`:222 · `ToolPage.build_form`:227 · `ToolPage.build_command`:230 · `ToolPage.validate`:233 · `ToolPage.after_finished`:243 · `ToolPage.extra_action_buttons`:246 · `ToolPage.add_row`:251 · `ToolPage.add_widget`:257 · `ToolPage.settings_card`:261 · `ToolPage.group_label`:271 · `ToolPage.section_heading`:277 · `ToolPage.grid_2col`:283 · `ToolPage.divider`:303 · `ToolPage.progress_from_line`:383 · `ToolPage.on_output_line`:413 · `ToolPage.log_text`:440 · `ToolPage.advance_batch`:444 · `ToolPage.clear_cards`:506 · `ToolPage.show_result`:511 · `ToolPage.record_artefact`:518 · `ToolPage.show_failure`:521 · `ToolPage.can_fix`:581 · `ToolPage.apply_fix`:586

### `src/updater.py` — 315 lines
In-app auto-update for Mariposa Studio (Strategy A: source overlay).

`current_version`:53 · `is_newer`:69 · `fetch_latest`:83 · `apply_update`:152 · `relaunch`:176 · `UpdateBanner`:229 · `UpdateBanner.present`:264 · `attach_updater`:308

### `src/widgets.py` — 891 lines
Reusable UI widgets for Mariposa Studio (cards, drop zones, controls, console view, app bar). Shared by every page.

`Card`:27 · `RaisedCard`:36 · `FormRow`:45 · `DropZone`:92 · `DropZone.value`:277 · `DropZone.set_value`:280 · `Segmented`:291 · `Field`:349 · `SettingRow`:365 · `ChipGroup`:414 · `ChipGroup.set_presets`:436 · `Switch`:464 · `ConsoleView`:513 · `ConsoleView.append_line`:528 · `AppBar`:546 · `AppBar.add_right`:576 · `AppBar.add_left`:579 · `Select`:625 · `AskDialog`:749 · `AskDialog.value`:844 · `ask_text`:866 · `ask_confirm`:879

### `src/widgets_status.py` — 563 lines
The job runner's honest surfaces: the log in daylight, and the two cards a finished job ends in.

`StateDot`:71 · `StateDot.set_state`:85 · `ProgressLine`:113 · `ProgressLine.start`:146 · `ProgressLine.stop`:155 · `ProgressLine.set_units`:158 · `ProgressLine.finish`:174 · `human_duration`:202 · `ResultCard`:213 · `FailureCard`:248 · `LogColumn`:286 · `LogColumn.set_state`:390 · `LogColumn.set_env`:405 · `LogColumn.set_units`:413 · `LogColumn.finish_progress`:416 · `LogColumn.append`:421 · `LogColumn.clear_log`:424 · `LogColumn.log_text`:427 · `LogColumn.show_card`:431 · `LogColumn.clear_card`:436 · `StatusStrip`:451 · `StatusStrip.set_state`:512 · `StatusStrip.set_units`:522 · `StatusStrip.finish_progress`:527 · `StatusStrip.set_detail`:530 · `StatusStrip.append`:533 · `StatusStrip.clear_log`:536 · `StatusStrip.log_text`:539 · `StatusStrip.show_card`:542 · `StatusStrip.clear_card`:547 · `StatusStrip.set_env`:555

## Build & test scripts (`scripts/`)

### `scripts/build_fonts.py` — 108 lines
Build the static brand TTFs Qt can load, from the variable woff2 sources.

`build`:78

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

### `scripts/test_clipcutter_gate.py` — 190 lines
The one thing Clip Cutter can still ask of a person, and how it asks.

`check`:49 · `make_project`:54 · `card_buttons`:155

### `scripts/test_clock.py` — 209 lines
Offline checks for speech_clock — no Qt, no network, no API key.

`check`:33

### `scripts/test_diagnostics.py` — 153 lines
Is the error report complete — and is it safe to paste?

`check`:41

### `scripts/test_export_geometry.py` — 106 lines
What the exporter inherits from the donor project, and what it must not.

`check`:32

### `scripts/test_failures.py` — 64 lines
Offline checks for the failure table (no Qt, no display needed).

_No public symbols._

### `scripts/test_fonts.py` — 94 lines
Assert the stylesheet gets the type it asks for, on THIS machine.

`check`:45

### `scripts/test_gemini.py` — 218 lines
Offline checks for `src/gemini.py`'s model chain and its error sentences.

`check`:36 · `http_error`:44 · `FakeTransport`:63 · `FakeTransport.urlopen`:72 · `FakeTransport.sleep`:81 · `run`:99 · `main`:118

### `scripts/test_packer.py` — 751 lines
Offline checks for script_packer — no Qt, no network, no API key.

`check`:50 · `sent`:58

### `scripts/test_portable.py` — 371 lines
Prove Clip Cutter finds its dependencies on a machine it has never seen.

`check`:37 · `probe`:43 · `draft`:86 · `wdraft`:106

### `scripts/test_release.py` — 242 lines
Prove the release zip is the app — before it is a release.

`check`:47 · `section`:54 · `build_archive`:58 · `reachable_modules`:83 · `icon_names`:111 · `wanted_font_files`:128 · `main`:141

### `scripts/test_settings.py` — 109 lines
Does the Settings screen actually reach the app?

`check`:44 · `source`:49

### `scripts/test_windows.py` — 305 lines
Prove the Windows-only code paths, from a machine that is not Windows.

`check`:53 · `section`:60 · `bare_text_opens`:75 · `test_encoding`:101 · `test_argv`:163 · `test_concat`:184 · `test_no_console`:200 · `test_paths`:219 · `test_installer`:261 · `main`:288

### `scripts/upsert_env.py` — 50 lines
Upsert a KEY=VALUE into tools/captions-de/.env, preserving every other line.

`upsert`:23 · `main`:42

## Bundled tool scripts (`tools/`) — separate processes, not imported

### `tools/captions-de/caption.py` — 2024 lines
Generate TikTok-style captions (SRT) from a video file. German is the default; Polish, French, Italian (plus English and Spanish) are selected with --language a

`text_width`:87 · `auto_hyphenate`:207 · `apply_auto_hyphenation`:227 · `join_soft_hyphens`:247 · `flatten_lines`:265 · `drop_midline_hyphens`:285 · `strip_punct`:296 · `clean_for_output`:302 · `normalize_case`:334 · `insert_compound_hyphens`:348 · `tokenize_for_packing`:356 · `pack_lines`:372 · `format_caption`:423 · `fmt_time`:427 · `get_video_duration`:437 · `run_whisperx`:463 · `load_words`:521 · `build_generic_prompt`:623 · `segment_with_ai`:728 · `review_grouping`:800 · `segment_heuristic`:975 · `compute_boundaries`:1006 · `fix_line_break`:1038 · `normalize_text_preserve_breaks`:1074 · `apply_canonical_terms`:1171 · `project_terms_block`:1218 · `finalize_caption`:1232 · `move_trailing_binders`:1421 · `split_emphasis_repeats`:1447 · `merge_orphans`:1489 · `merge_split_numbers`:1597 · `merge_short_durations`:1624 · `enforce_single_line`:1785 · `enforce_two_lines`:1793 · `learn_and_relabel_case`:1803 · `recase_with_ai`:1881 · `write_srt`:1920 · `main`:1927

### `tools/captions-de/caption_qa.py` — 342 lines
PROTOTYPE — Approach B: Gemini-based caption QA pass.

`parse_srt`:45 · `qa_check`:171 · `main`:277

### `tools/captions-de/install.py` — 167 lines
Cross-platform installer for the caption tool.

`section`:23 · `check_python`:30 · `check_ffmpeg`:42 · `make_venv`:75 · `venv_python`:103 · `install_whisperx`:124 · `write_env_example`:133 · `main`:145

### `tools/clip-cutter/scripts/analyze_silence.py` — 155 lines
Analyze an ordered list of UGC clips: detect display geometry + fps, measure the speech envelope of each clip, and emit src/clips.ts with frame-accurate trims t

`probe`:26 · `load_audio`:46 · `rms_envelope`:54 · `silence_runs`:64 · `speech_bounds`:80 · `nearest_fps`:96 · `main`:100

### `tools/clip-cutter/scripts/build.py` — 292 lines
build.py — the one idempotent command. Rebuilds the minimum, converges, reports.

`refresh_sources`:39 · `est_seconds`:53 · `print_frontier`:62 · `record`:80 · `run_node`:91 · `check_font`:107 · `main`:125

### `tools/clip-cutter/scripts/build_segment_audio.py` — 86 lines
Build one trimmed-concatenated WAV per unique segment (each hook, the body, each CTA) from plan.json, so each segment is transcribed ONCE (SOP: caption the base

`build_segment`:24 · `main`:68

### `tools/clip-cutter/scripts/buildgraph.py` — 303 lines
The build DAG: nodes, want-hashes, staleness classification, rebuild frontier.

`Node`:35 · `build_nodes`:51 · `combo_out`:154 · `unique_clips`:160 · `config_core`:170 · `edits_signature`:176 · `src_fingerprint`:182 · `compute_wants`:205 · `classify`:224 · `expected_files`:285

### `tools/clip-cutter/scripts/caption_segments.py` — 116 lines
Caption every segment WAV in parallel, with automatic stale-cache invalidation.

`caption_dir`:37 · `main`:95

### `tools/clip-cutter/scripts/caption_spec.py` — 84 lines
The caption look, as numbers. MUST mirror template/src/caption-style.ts.

`spec_dict`:78

### `tools/clip-cutter/scripts/caption_tool.py` — 78 lines
Bridge to the Mariposa captions tool's OWN line-layout functions.

`available`:43 · `text_width`:51 · `line_w_max`:55 · `pack_lines`:59 · `format_caption`:65 · `fits`:70 · `widest`:76

### `tools/clip-cutter/scripts/check_font.py` — 20 lines
Verify the vendored caption font is usable, and print the derived ASS numbers.

_No public symbols._

### `tools/clip-cutter/scripts/concat_combos.py` — 95 lines
Build every combo by concatenating pre-rendered, captioned segment videos.

`concat_one`:32 · `out_path`:68 · `main`:74

### `tools/clip-cutter/scripts/detect_takes.py` — 113 lines
Double-take remover — KEEP THE LAST clean take.

`whisperx_words`:26 · `norm`:46 · `last_take_start`:50 · `main`:82

### `tools/clip-cutter/scripts/edits.py` — 230 lines
edits.json — the human+auto edit overlay that survives re-planning.

`path_for`:27 · `load_edits`:31 · `save_edits`:42 · `next_cut_id`:46 · `append_cut`:57 · `project`:85 · `bake_src_cuts`:148 · `apply_removals_to_clips`:180 · `effective_plan`:210

### `tools/clip-cutter/scripts/export_capcut.py` — 1360 lines
Export a caption-ugc edit as a CapCut project, for manual revision by an editor.

`gid`:49 · `us`:53 · `newest_template`:60 · `place_media`:112 · `describe_media`:141 · `make_cover`:152 · `cover_source`:177 · `as_shot`:204 · `pick_video_template`:234 · `pick_text_template`:261 · `template_token`:291 · `clone_extras`:322 · `build_text_content`:341 · `make_caption`:359 · `house_layout`:396 · `check_caption_widths`:421 · `make_headline`:458 · `wrap_headline`:484 · `build_timeline`:501 · `make_compound`:610 · `collect_media`:667 · `write_project`:687 · `meta_path`:744 · `reclaim`:763 · `register`:791 · `main`:845

### `tools/clip-cutter/scripts/fix.py` — 312 lines
fix.py — the fast correction loop. One short command per fix, then `build.py`.

`parse_time`:35 · `resolve_target`:45 · `cmd_where`:79 · `cmd_spell`:116 · `cmd_cue`:153 · `cmd_cut`:185 · `cmd_ls`:226 · `cmd_undo`:242 · `cmd_status`:257 · `main`:262

### `tools/clip-cutter/scripts/font_spec.py` — 105 lines
Font metrics for the ASS backend + the guards that stop a silent substitution.

`FontError`:20 · `FontSpec`:24 · `FontSpec.cell_em`:40 · `FontSpec.ass_fontsize`:43 · `FontSpec.baseline_correction`:46 · `FontSpec.summary`:59 · `load_font_spec`:65 · `assert_burnable`:95

### `tools/clip-cutter/scripts/hashing.py` — 76 lines
Content hashing + atomic writes for the incremental build.

`sample_hash`:19 · `full_hash`:30 · `content_hash`:38 · `h_json`:45 · `witness`:51 · `atomic_write_text`:63 · `atomic_write_json`:75

### `tools/clip-cutter/scripts/headline_style.py` — 83 lines
The red headline box, extracted verbatim from the C96 CapCut project.

`content`:70

### `tools/clip-cutter/scripts/migrate.py` — 151 lines
Adopt an existing project into the incremental build without redoing work.

`recover_cuts`:30 · `main`:58

### `tools/clip-cutter/scripts/plan_creative.py` — 195 lines
Plan a full creative — a PURE function of (config, clip probes).

`load_probe_cache`:41 · `probe_clip`:52 · `main`:66

### `tools/clip-cutter/scripts/plan_io.py` — 100 lines
The plan.json contract — one owner for the schema and the segments.ts emitter.

`seg_recipe`:21 · `segment_spans`:34 · `total_frames`:44 · `write_segments_ts`:48 · `write_plan`:57 · `load_plan`:61 · `validate_plan`:66

### `tools/clip-cutter/scripts/portable.py` — 595 lines
Where everything is, on whatever machine this is running on.

`ffmpeg`:111 · `ffprobe`:122 · `caption_tool`:131 · `cropper`:136 · `whisperx_python`:142 · `no_window_kwargs`:162 · `concat_line`:167 · `draft_file`:213 · `draft_file_name`:226 · `capcut_projects`:266 · `capcut_app`:338 · `capcut_font`:443 · `capcut_installed`:487 · `reset_cache`:493 · `capcut_template_count`:510 · `preflight`:520 · `missing`:561 · `require`:566

### `tools/clip-cutter/scripts/report.py` — 293 lines
Build manifest.json + review.tsv + a short review.md.

`font_spec_or_none`:45 · `measure`:54 · `flags_for`:64 · `main`:96 · `derive_manual`:258

### `tools/clip-cutter/scripts/run_clip_cutter.py` — 124 lines
One command behind Mariposa Studio's Clip Cutter: config.json -> CapCut project.

`step`:25 · `run`:29 · `main`:40

### `tools/clip-cutter/scripts/run_creative.py` — 110 lines
One-command entry point: scaffold a project if needed, then hand off to build.py.

`sh`:31 · `scaffold`:36 · `main`:83

### `tools/clip-cutter/scripts/selftest.py` — 219 lines
Fast, read-only assertions against a built fixture. No renders, no mutation.

`check`:25

### `tools/clip-cutter/scripts/srt.py` — 304 lines
SRT parsing / serialising / retiming — the one implementation.

`parse_srt`:18 · `load_srt`:35 · `fmt_ms`:40 · `dump_srt`:48 · `remap_cues`:55 · `rewrap`:90 · `partially_cut`:126 · `split_wide_cues`:151 · `align_cues_to_boundaries`:224

### `tools/clip-cutter/scripts/srt2ass.py` — 204 lines
SRT -> ASS, reproducing template/src/caption-style.ts exactly.

`esc_filter_path`:39 · `ms_to_ass`:44 · `line_y`:59 · `measure_line`:67 · `ass_alpha`:98 · `build_ass`:103 · `srt_file_to_ass`:163 · `verify_font`:172

### `tools/clip-cutter/scripts/state.py` — 138 lines
state.json — the build's memory, plus locking and orphan pruning.

`path_for`:27 · `empty`:31 · `load_state`:36 · `save_state`:52 · `Lock`:56 · `find_orphans`:99 · `prune`:126 · `stamp`:137

### `tools/clip-cutter/scripts/steps.py` — 393 lines
Executors: one run_<kind>() per node kind. Every one writes <out>.part then os.replace()s it, so a kill -9 never leaves a half-written artifact adopted.

`sh`:47 · `run_probe`:67 · `run_proxy`:80 · `run_plan`:105 · `run_bundle`:112 · `run_wav`:126 · `run_srt`:134 · `clip_bounds_ms`:148 · `write_render_inputs`:158 · `write_srts_ts`:203 · `run_clean_batch`:218 · `run_cut`:245 · `run_ass`:297 · `run_burn`:334 · `run_combo`:367 · `run_crop`:382 · `run_report`:391

### `tools/clip-cutter/scripts/tighten_gaps.py` — 187 lines
Auto dead-air removal — acoustic detection, word-timing guard rail.

`load_words`:55 · `find_holds`:68 · `propose_cuts`:100 · `main`:145

### `tools/extract-frame/extract_last_frame.py` — 91 lines
Extract frames from a video.

`extract`:24

### `tools/flow-cropper/crop.py` — 882 lines
Flow Cropper — 9:16 → 4:5 batch crop + smart rename.

`normalize_creator`:85 · `safe_print`:98 · `rename_with_retry`:115 · `find_ffmpeg`:151 · `detect_creative_id`:170 · `select_encoder`:213 · `crop_to_4x5`:259 · `normalize_creative_id`:272 · `fs_safe`:292 · `creative_name`:306 · `simple_name`:319 · `process_folder`:348 · `detect_structure`:446 · `run`:487 · `undo_last`:532 · `pick_folder`:593 · `ask_text`:611 · `ask_choice`:634 · `alert`:677 · `interactive`:705 · `run_with`:748 · `main`:816
