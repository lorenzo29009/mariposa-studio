# Symbol index

Generated — do not edit by hand. Refresh with:

```bash
./venv/bin/python scripts/gen_index.py
```

Public top-level classes, methods and functions only; anything named with a leading `_` is internal to its module. Line numbers are a starting point, not a promise.

## The app (`src/`)

### `src/animator_build.py` — 444 lines
Script Animator — a build, from the button to the scenes.

`BuildRunner`:72

### `src/animator_common.py` — 118 lines
Script Animator — the constants, the session log and the one Qt helper its modules share.

`fit_scroll_content`:59 · `log_save`:94 · `log_load`:107

### `src/animator_page.py` — 636 lines
Script Animator page: a structured ad script (hook variations, body, CTA variants) -> duration-slotted scene prompts.

`AnimatorPage`:59 · `AnimatorPage.language_name`:439 · `AnimatorPage.tail`:442 · `AnimatorPage.pronunciation`:445

### `src/animator_panel.py` — 450 lines
Script Animator - the always-visible floating step-through panel.

`AnimatorFloatPanel`:68 · `AnimatorFloatPanel.update_scenes`:315 · `AnimatorFloatPanel.set_index`:321 · `AnimatorFloatPanel.set_generated`:399

### `src/animator_pipeline.py` — 689 lines
Script Animator - the Gemini passes, and the worker that runs a build.

`ScenePipelineWorker`:367 · `ScenePipelineWorker.abandon`:410 · `ScenePipelineWorker.run`:442

### `src/animator_plan.py` — 121 lines
Script Animator — what a build will take, priced before it starts.

`count_sentences`:74 · `read_prior`:80 · `timing_prior`:88 · `cut_prior`:92 · `review_prior`:96 · `leg`:100 · `plan`:107

### `src/animator_runtime.py` — 280 lines
Script Animator, stage one: the spoken length while you write.

`RuntimeColumn`:87

### `src/animator_scenes.py` — 416 lines
Script Animator, stage two: the cut.

`ScenesStage`:37 · `ScenesStage.open_float_panel`:389

### `src/animator_widgets.py` — 496 lines
Script Animator - the row and card widgets of the two stages.

`BlockRow`:29 · `BlockRow.value`:100 · `BlockRow.set_value`:103 · `BlockRow.set_tag`:106 · `BlockRow.tag`:109 · `BlockRow.set_timing`:112 · `BlockRow.set_removable`:131 · `BlockRow.set_last`:134 · `FillMeter`:210 · `SceneCard`:258 · `SceneCard.set_generated`:404 · `SceneCard.offer_split`:408 · `SceneCard.refresh_prompt`:479 · `SceneCard.set_expanded`:483 · `SceneCard.set_selected`:487

### `src/camera_page.py` — 673 lines
Camera Prompts page: a searchable gallery of shot/angle references that composes a Gemini prompt.

`GeminiWorker`:72 · `GeminiWorker.start`:92 · `GeminiWorker.is_alive`:95 · `GeminiWorker.outcome`:98 · `CameraPromptsPage`:114 · `CameraPromptsPage.is_busy`:574

### `src/camera_widgets.py` — 455 lines
Camera Prompts - the gallery widgets.

`RoundedImage`:64 · `PromptCard`:97 · `PromptCard.set_selected`:154 · `FlowLayout`:182 · `FlowLayout.count`:194 · `CategorySection`:257 · `CategorySection.add_card`:293 · `CategorySection.reflow`:296 · `FuseSheet`:325 · `FuseSheet.place`:379 · `FuseSheet.begin_progress`:388 · `FuseSheet.end_progress`:393 · `Toast`:398 · `Toast.place`:422 · `Toast.flash`:434

### `src/caption_compare.py` — 632 lines
ComparePanel — checking finished captions against the script.

`gemini_busy`:80 · `ComparePanel`:101 · `ComparePanel.is_busy`:227 · `ComparePanel.set_srt`:234 · `ComparePanel.set_language`:243

### `src/captions_page.py` — 648 lines
Captions DE: WhisperX + Gemini -> .srt, run in the separate WhisperX venv.

`whisperx_arch_ok`:46 · `CaptionsPage`:74 · `CaptionsPage.build_form`:140 · `CaptionsPage.validate`:284 · `CaptionsPage.build_command`:302 · `CaptionsPage.clip_plan`:355 · `CaptionsPage.clip_prior`:384 · `CaptionsPage.plan_batch`:389 · `CaptionsPage.plan_run`:400 · `CaptionsPage.on_progress`:416 · `CaptionsPage.on_output_line`:477 · `CaptionsPage.advance_batch`:485 · `CaptionsPage.env_lines`:494 · `CaptionsPage.can_fix`:510 · `CaptionsPage.apply_fix`:513 · `CaptionsPage.after_finished`:565 · `CaptionsPage.progress_from_line`:613 · `CaptionsPage.is_busy`:645

### `src/clip_cutter_page.py` — 1176 lines
Clip Cutter: assemble a UGC creative from a clip folder and hand it to CapCut.

`hook_number`:187 · `ClipCutterPage`:229 · `ClipCutterPage.build_form`:329 · `ClipCutterPage.validate`:937 · `ClipCutterPage.job_facts`:967 · `ClipCutterPage.repro_files`:998 · `ClipCutterPage.build_command`:1031 · `ClipCutterPage.after_finished`:1083 · `ClipCutterPage.can_fix`:1118 · `ClipCutterPage.apply_fix`:1167

### `src/clip_cutter_progress.py` — 365 lines
Clip Cutter's progress: one bar for a run whose long stage is two captioners working side by side.

`CaptionLanes`:75 · `CaptionLanes.plan`:94 · `CaptionLanes.start`:113 · `CaptionLanes.event`:118 · `CaptionLanes.finish`:128 · `CaptionLanes.share`:146 · `CaptionLanes.pace`:152 · `CaptionLanes.expected`:170 · `CaptionLanes.fraction`:200 · `CaptionLanes.remaining`:211 · `CaptionLanes.running`:230 · `CaptionLanes.sentence`:234 · `CaptionLanes.learn`:242 · `ClipCutterProgress`:248 · `ClipCutterProgress.on_progress`:279 · `ClipCutterProgress.on_output_line`:296 · `ClipCutterProgress.progress_from_line`:309

### `src/clip_cutter_widgets.py` — 536 lines
Clip Cutter's drag-and-drop assembly widgets.

`video_urls`:39 · `register_thumb`:64 · `ClipChip`:102 · `PoolCard`:162 · `DropArea`:180 · `DropArea.names`:223 · `DropArea.set_names`:226 · `DropArea.add_name`:230 · `DropArea.remove_name`:236 · `BodyStrip`:376 · `SlotRow`:405 · `SlotRow.set_code`:466 · `SlotRow.names`:470 · `SlotRow.headline_text`:473 · `DropCue`:477 · `DashedButton`:530

### `src/core.py` — 478 lines
Shared foundation for Mariposa Studio: paths, the .env helpers, and the small platform/icon helpers used across every page module.

`studio_python`:153 · `make_qprocess_env`:159 · `chevron_icon`:177 · `arrow_icon`:184 · `reveal_in_finder`:189 · `notify`:216 · `bring_back_main_window`:277 · `open_folder`:299 · `make_nonactivating_panel`:316 · `ensure_windows_shortcut`:398 · `read_env_value`:442 · `gemini_model_override`:451 · `write_env_value`:462

### `src/design.py` — 342 lines
Mariposa Studio — Design System (single source of truth).

`load_fonts`:46 · `tint`:68 · `apply_shadow`:255 · `svg_icon`:293 · `svg_pixmap`:298 · `app_accent`:303 · `primary_button_style`:309 · `brand_pixmap`:318

### `src/diagnostics.py` — 690 lines
One error report, complete enough to fix a bug from — and safe to paste.

`redact`:89 · `note_log`:107 · `note_error`:114 · `last_error`:126 · `note_job`:130 · `note_job_finished`:158 · `report`:356 · `save_report`:455 · `save_bundle`:467 · `share_report`:509 · `shared_line`:538 · `start_log`:627 · `install_hooks`:652

### `src/extract_frame_page.py` — 401 lines
Extract Frame: pull the last, first, random or every-N-seconds frame (OpenCV).

`BatchCard`:46 · `ExtractFramePage`:148 · `ExtractFramePage.build_form`:189 · `ExtractFramePage.validate`:332 · `ExtractFramePage.build_command`:346 · `ExtractFramePage.plan_run`:358 · `ExtractFramePage.on_progress`:364 · `ExtractFramePage.progress_from_line`:377 · `ExtractFramePage.after_finished`:384

### `src/failures.py` — 213 lines
Turning a stack trace into a sentence and a button.

`Failure`:26 · `classify`:175 · `last_error_line`:185 · `describe`:202

### `src/first_run.py` — 337 lines
First run — one thing to paste in, and a look at what installs itself.

`should_show`:44 · `mark_done`:55 · `run_installer`:94 · `run_whisperx_installer`:106 · `FirstRunPage`:149

### `src/flow_cropper_page.py` — 684 lines
Flow Cropper: batch 9:16 -> 4:5 crops via ffmpeg, named from the briefing.

`FlowCropperPage`:178 · `FlowCropperPage.build_form`:191 · `FlowCropperPage.extra_action_buttons`:335 · `FlowCropperPage.ad_format_value`:398 · `FlowCropperPage.validate`:425 · `FlowCropperPage.on_output_line`:484 · `FlowCropperPage.plan_run`:507 · `FlowCropperPage.on_progress`:512 · `FlowCropperPage.progress_from_line`:549 · `FlowCropperPage.build_command`:561 · `FlowCropperPage.after_finished`:584 · `FlowCropperPage.can_fix`:639 · `FlowCropperPage.apply_fix`:642

### `src/gemini.py` — 442 lines
Gemini over plain HTTPS — the one transport the app uses.

`key_tokens`:102 · `clean_key`:112 · `key_shape`:134 · `ssl_context`:152 · `GeminiError`:209 · `models_to_try`:232 · `generate_text`:394 · `generate_json`:413

### `src/jobs.py` — 135 lines
What is running right now, and what happens when something stops.

`register`:42 · `unregister`:47 · `busy`:54 · `quitting`:68 · `quit_now`:72 · `finished`:100

### `src/launcher.py` — 514 lines
The shell: the home grid of tools and the ⌘K overlay.

`AppIcon`:90 · `AppIcon.event`:150 · `LauncherPage`:177 · `LauncherPage.focus_first`:259 · `SpotlightOverlay`:333 · `SpotlightOverlay.open`:459

### `src/make_icon.py` — 176 lines
Render AppIcon.icns for the Mariposa Studio .app bundle.

`draw_icon`:66 · `write_multi_ico`:115 · `main`:135

### `src/progress.py` — 616 lines
Where a running job is, and how long it has left — a route, not a guess.

`glide`:56 · `Leg`:69 · `Leg.actual`:93 · `Route`:99 · `Route.index`:121 · `Route.leg`:127 · `Route.extend`:131 · `Route.replan`:145 · `Route.begin`:163 · `Route.enter`:167 · `Route.current`:195 · `Route.advance`:202 · `Route.complete`:229 · `Route.skip`:235 · `Route.attach`:240 · `Route.finish`:247 · `Route.units`:254 · `Route.pace`:289 · `Route.position`:367 · `Route.remaining`:416 · `Route.elapsed`:440 · `Route.learn`:446 · `History`:459 · `History.expect`:484 · `History.learn`:493 · `History.save`:515 · `configure`:535 · `history`:542 · `phrase_left`:561 · `Countdown`:578 · `Countdown.reset`:588 · `Countdown.update`:593

### `src/progress_wire.py` — 159 lines
The wire between a tool script and its progress bar.

`LineReader`:33 · `LineReader.feed`:50 · `LineReader.flush`:81 · `parse`:90 · `emit`:111 · `apply`:130

### `src/script_packer.py` — 877 lines
Scene logic for the Script Animator — pure logic, no Qt, no network.

`ceiling`:96 · `split_long_sentence`:238 · `performance_beats`:277 · `pause_between`:302 · `analytic_seconds`:324 · `timing_source`:348 · `estimate_seconds`:354 · `nearest_slot`:368 · `assign_duration`:384 · `pack_sentences`:484 · `collapse_to_one`:522 · `relabel`:531 · `merge_scenes`:555 · `split_scene`:576 · `best_seam`:593 · `set_duration`:616 · `overruns`:636 · `flag_for`:646 · `ends_mid_sentence`:707 · `finalise_block`:778 · `pack_block`:827 · `build_prompt`:851 · `build_markdown`:866 · `format_runtime`:875

### `src/script_text.py` — 644 lines
The language layer under the Animator: words, sentences and seams.

`count_syllables`:145 · `split_sentences`:152 · `word_forms`:180 · `in_vocabulary`:198 · `fragment_sentence`:364 · `infer_link`:388 · `openers_for`:479 · `numeral_re`:532 · `pronunciation_for`:560 · `parse_pronunciation`:570 · `apply_pronunciation`:587 · `leftover_symbols`:605 · `verbatim_gaps`:619

### `src/session.py` — 97 lines
What this launch has made — held in memory, and only in memory.

`Artefact`:29 · `Artefact.is_dir`:37 · `record`:49 · `items`:59 · `clear`:67 · `note_gemini`:74 · `gemini_note`:79 · `ago`:87

### `src/settings_page.py` — 551 lines
Settings — the few things that are the user's to set, and nothing else.

`pref`:51 · `set_pref`:58 · `notify_if_enabled`:62 · `folder_size`:77 · `human_size`:98 · `stale_entries`:105 · `SettingsPage`:138

### `src/speech_clock.py` — 455 lines
How long a line takes to say — **measured**, not estimated.

`Engine`:78 · `Engine.path`:118 · `Engine.available`:131 · `Engine.voice_for`:134 · `Engine.command`:138 · `engine_named`:189 · `available_engine`:193 · `reset_engine_probe`:200 · `engine_note`:205 · `load_calibration`:258 · `calibration_for`:269 · `wav_speech_seconds`:294 · `flush_cache`:353 · `clear_cache`:368 · `measure_raw`:386 · `measure`:431 · `duration_of`:444

### `src/studio.py` — 413 lines
Mariposa Studio - one hub for the editing-pipeline tools.

`MainWindow`:96 · `MainWindow.bring_back`:218 · `main`:379

### `src/stylesheet.py` — 1077 lines
The app-wide QSS, built from the tokens in `design`.

`build_stylesheet`:34 · `font_pairs`:1016 · `font_health`:1039 · `font_problems`:1073

### `src/tool_page.py` — 731 lines
`ToolPage` — the base every subprocess-backed tool page is built on.

`ToolPage`:141 · `ToolPage.build_side`:276 · `ToolPage.env_lines`:280 · `ToolPage.set_env_lines`:286 · `ToolPage.build_form`:291 · `ToolPage.build_command`:294 · `ToolPage.validate`:297 · `ToolPage.job_facts`:307 · `ToolPage.repro_files`:318 · `ToolPage.after_finished`:328 · `ToolPage.is_busy`:331 · `ToolPage.extra_action_buttons`:338 · `ToolPage.add_row`:343 · `ToolPage.add_widget`:349 · `ToolPage.settings_card`:353 · `ToolPage.group_label`:363 · `ToolPage.section_heading`:369 · `ToolPage.grid_2col`:375 · `ToolPage.divider`:395 · `ToolPage.progress_from_line`:527 · `ToolPage.on_output_line`:558 · `ToolPage.log_text`:577 · `ToolPage.advance_batch`:581 · `ToolPage.clear_cards`:657 · `ToolPage.show_result`:662 · `ToolPage.record_artefact`:670 · `ToolPage.show_failure`:673 · `ToolPage.can_fix`:715 · `ToolPage.apply_fix`:720

### `src/tool_progress.py` — 119 lines
`RunProgress` — how a `ToolPage` run reports where it is.

`RunProgress`:31 · `RunProgress.plan_run`:33 · `RunProgress.plan_batch`:38 · `RunProgress.progress_events`:44 · `RunProgress.on_progress`:50

### `src/updater.py` — 329 lines
In-app auto-update for Mariposa Studio (Strategy A: source overlay).

`current_version`:53 · `is_newer`:69 · `fetch_latest`:83 · `apply_update`:157 · `relaunch`:181 · `UpdateBanner`:234 · `UpdateBanner.is_busy`:273 · `UpdateBanner.present`:276 · `attach_updater`:322

### `src/widgets.py` — 891 lines
Reusable UI widgets for Mariposa Studio (cards, drop zones, controls, console view, app bar). Shared by every page.

`Card`:27 · `RaisedCard`:36 · `FormRow`:45 · `DropZone`:92 · `DropZone.value`:277 · `DropZone.set_value`:280 · `Segmented`:291 · `Field`:349 · `SettingRow`:365 · `ChipGroup`:414 · `ChipGroup.set_presets`:436 · `Switch`:464 · `ConsoleView`:513 · `ConsoleView.append_line`:528 · `AppBar`:546 · `AppBar.add_right`:576 · `AppBar.add_left`:579 · `Select`:625 · `AskDialog`:749 · `AskDialog.value`:844 · `ask_text`:866 · `ask_confirm`:879

### `src/widgets_status.py` — 634 lines
The job runner's honest surfaces: the log in daylight, and the two cards a finished job ends in.

`StateDot`:73 · `StateDot.set_state`:87 · `ProgressLine`:115 · `ProgressLine.start`:164 · `ProgressLine.is_running`:178 · `ProgressLine.started_at`:181 · `ProgressLine.resume`:185 · `ProgressLine.stop`:194 · `ProgressLine.track`:198 · `ProgressLine.route`:206 · `ProgressLine.set_units`:209 · `ProgressLine.finish`:218 · `human_duration`:258 · `ResultCard`:269 · `FailureCard`:308 · `LogColumn`:346 · `LogColumn.set_state`:450 · `LogColumn.set_env`:468 · `LogColumn.set_units`:476 · `LogColumn.track`:479 · `LogColumn.finish_progress`:482 · `LogColumn.append`:487 · `LogColumn.clear_log`:490 · `LogColumn.log_text`:493 · `LogColumn.show_card`:497 · `LogColumn.clear_card`:502 · `StatusStrip`:517 · `StatusStrip.set_state`:578 · `StatusStrip.set_units`:591 · `StatusStrip.track`:594 · `StatusStrip.finish_progress`:597 · `StatusStrip.set_detail`:601 · `StatusStrip.append`:604 · `StatusStrip.clear_log`:607 · `StatusStrip.log_text`:610 · `StatusStrip.show_card`:613 · `StatusStrip.clear_card`:618 · `StatusStrip.set_env`:626

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

### `scripts/test_animator_progress.py` — 507 lines
The Script Animator's build progress (offscreen Qt, no network).

`check`:69 · `pump`:75 · `emit`:80 · `new_page`:95 · `packed_for`:106 · `Clock`:113 · `copy_of`:141 · `fake_gemini`:381

### `scripts/test_captions.py` — 584 lines
Offline checks for the captions tool's language layer (no Qt, no WhisperX).

`check`:27 · `moved`:35 · `repaired_es`:335 · `repaired`:435

### `scripts/test_captions_progress.py` — 577 lines
Captions progress: caption.py's route, WhisperX's boundaries, the page's words.

`check`:45 · `section`:51 · `Chunks`:125 · `Chunks.read1`:135 · `relay`:139 · `caption_output`:336 · `single_page`:363 · `feed_through`:384

### `scripts/test_clipcutter_gate.py` — 466 lines
The one thing Clip Cutter can still ask of a person, and how it asks.

`check`:61 · `make_project`:66 · `card_buttons`:167 · `caption_run`:251 · `Clock`:331 · `ev`:338 · `child_plan`:342 · `fresh_page`:351 · `recorded_run`:361 · `jump`:444

### `scripts/test_clock.py` — 209 lines
Offline checks for speech_clock — no Qt, no network, no API key.

`check`:33

### `scripts/test_diagnostics.py` — 230 lines
Is the error report complete — and is it safe to paste?

`check`:41

### `scripts/test_export_geometry.py` — 106 lines
What the exporter inherits from the donor project, and what it must not.

`check`:32

### `scripts/test_failures.py` — 67 lines
Offline checks for the failure table (no Qt, no display needed).

_No public symbols._

### `scripts/test_flow_cropper.py` — 455 lines
Flow Cropper's folder discovery — the layouts a run is actually handed.

`check`:19 · `clips`:27 · `labels`:33 · `sources`:37 · `make_clip`:198 · `crop_run`:208 · `events`:215 · `parts`:219

### `scripts/test_fonts.py` — 94 lines
Assert the stylesheet gets the type it asks for, on THIS machine.

`check`:45

### `scripts/test_gemini.py` — 377 lines
Offline checks for `src/gemini.py`'s model chain and its error sentences.

`check`:36 · `http_error`:44 · `FakeTransport`:63 · `FakeTransport.urlopen`:75 · `FakeTransport.sleep`:87 · `run`:105 · `main`:124

### `scripts/test_packer.py` — 824 lines
Offline checks for script_packer — no Qt, no network, no API key.

`check`:50 · `sent`:58

### `scripts/test_portable.py` — 371 lines
Prove Clip Cutter finds its dependencies on a machine it has never seen.

`check`:37 · `probe`:43 · `draft`:86 · `wdraft`:106

### `scripts/test_progress.py` — 287 lines
The progress engine, driven by a fake clock — no Qt, no jobs.

`check`:25 · `Clock`:31 · `run`:39

### `scripts/test_progress_pages.py` — 389 lines
Extract Frame and Camera Prompts report where they are (offscreen, no network).

`check`:58 · `spin`:64

### `scripts/test_release.py` — 242 lines
Prove the release zip is the app — before it is a release.

`check`:47 · `section`:54 · `build_archive`:58 · `reachable_modules`:83 · `icon_names`:111 · `wanted_font_files`:128 · `main`:141

### `scripts/test_settings.py` — 431 lines
Does the Settings screen actually reach the app?

`check`:53 · `source`:58 · `pump`:62 · `fake_job`:241 · `fake_ask`:347

### `scripts/test_toolpage.py` — 165 lines
What a job runner must survive (offscreen Qt, no real job).

`check`:31

### `scripts/test_windows.py` — 346 lines
Prove the Windows-only code paths, from a machine that is not Windows.

`check`:53 · `section`:60 · `bare_text_opens`:75 · `test_encoding`:101 · `test_argv`:167 · `test_concat`:188 · `test_no_console`:204 · `test_stages_speak`:223 · `test_paths`:259 · `test_installer`:301 · `main`:328

### `scripts/upsert_env.py` — 50 lines
Upsert a KEY=VALUE into tools/captions-de/.env, preserving every other line.

`upsert`:23 · `main`:42

## Bundled tool scripts (`tools/`) — separate processes, not imported

### `tools/captions-de/caption.py` — 3347 lines
Generate TikTok-style captions (SRT) from a video file. German is the default; English, Polish, French, Italian and Spanish (as spoken in Spain) are selected wi

`text_width`:93 · `auto_hyphenate`:213 · `apply_auto_hyphenation`:240 · `join_soft_hyphens`:260 · `flatten_lines`:278 · `drop_midline_hyphens`:298 · `normalize_apostrophes`:318 · `strip_punct`:322 · `clean_for_output`:328 · `normalize_case`:363 · `insert_compound_hyphens`:377 · `tokenize_for_packing`:385 · `pack_lines`:401 · `format_caption`:452 · `fmt_time`:456 · `get_video_duration`:474 · `gemini_legs`:572 · `asr_prior`:589 · `align_prior`:594 · `reask_prior`:599 · `progress_plan`:606 · `parse_download`:667 · `whisperx_events`:681 · `relay_output`:724 · `run_whisperx`:797 · `find_gaps`:892 · `repair_gaps`:928 · `load_words`:1055 · `build_generic_prompt`:1198 · `segment_with_ai`:1311 · `review_grouping`:1383 · `segment_heuristic`:1573 · `compute_boundaries`:1604 · `caption_spans`:1626 · `fix_line_break`:1663 · `normalize_text_preserve_breaks`:1719 · `apply_canonical_terms`:1886 · `repair_terms_with_ai`:2017 · `project_terms_block`:2103 · `rendered_width`:2139 · `finalize_caption`:2144 · `layout_caption`:2149 · `move_trailing_binders`:2533 · `split_emphasis_repeats`:2569 · `merge_orphans`:2620 · `merge_split_numbers`:2739 · `merge_short_durations`:2766 · `enforce_single_line`:3063 · `enforce_two_lines`:3072 · `learn_and_relabel_case`:3083 · `recase_with_ai`:3161 · `write_srt`:3200 · `main`:3209

### `tools/captions-de/caption_qa.py` — 410 lines
Gemini-based caption QA pass — finished captions vs the briefing.

`parse_srt`:68 · `qa_check`:223 · `main`:331

### `tools/captions-de/install.py` — 167 lines
Cross-platform installer for the caption tool.

`section`:23 · `check_python`:30 · `check_ffmpeg`:42 · `make_venv`:75 · `venv_python`:103 · `install_whisperx`:124 · `write_env_example`:133 · `main`:145

### `tools/clip-cutter/scripts/analyze_silence.py` — 173 lines
Analyze an ordered list of UGC clips: detect display geometry + fps, measure the speech envelope of each clip, and emit src/clips.ts with frame-accurate trims t

`ProbeError`:26 · `probe`:30 · `load_audio`:64 · `rms_envelope`:72 · `silence_runs`:82 · `speech_bounds`:98 · `nearest_fps`:114 · `main`:118

### `tools/clip-cutter/scripts/build.py` — 292 lines
build.py — the one idempotent command. Rebuilds the minimum, converges, reports.

`refresh_sources`:39 · `est_seconds`:53 · `print_frontier`:62 · `record`:80 · `run_node`:91 · `check_font`:107 · `main`:125

### `tools/clip-cutter/scripts/build_segment_audio.py` — 102 lines
Build one trimmed-concatenated WAV per unique segment (each hook, the body, each CTA) from plan.json, so each segment is transcribed ONCE (SOP: caption the base

`ffmpeg`:24 · `build_segment`:41 · `main`:84

### `tools/clip-cutter/scripts/buildgraph.py` — 303 lines
The build DAG: nodes, want-hashes, staleness classification, rebuild frontier.

`Node`:35 · `build_nodes`:51 · `combo_out`:154 · `unique_clips`:160 · `config_core`:170 · `edits_signature`:176 · `src_fingerprint`:182 · `compute_wants`:205 · `classify`:224 · `expected_files`:285

### `tools/clip-cutter/scripts/caption_segments.py` — 336 lines
Caption every segment WAV in parallel, with automatic stale-cache invalidation.

`lane_prior`:67 · `makespan`:72 · `wav_seconds`:82 · `lanes_event`:92 · `caption_dir`:222 · `main`:313

### `tools/clip-cutter/scripts/caption_spec.py` — 84 lines
The caption look, as numbers. MUST mirror template/src/caption-style.ts.

`spec_dict`:78

### `tools/clip-cutter/scripts/caption_tool.py` — 110 lines
Bridge to the Mariposa captions tool's OWN line-layout functions.

`available`:43 · `text_width`:51 · `line_w_max`:55 · `pack_lines`:59 · `format_caption`:65 · `flatten`:77 · `two_line_pieces`:83 · `set_language`:95 · `fits`:102 · `widest`:108

### `tools/clip-cutter/scripts/check_font.py` — 20 lines
Verify the vendored caption font is usable, and print the derived ASS numbers.

_No public symbols._

### `tools/clip-cutter/scripts/close_gaps.py` — 185 lines
Ripple the holes out of a CapCut timeline: no dead space between clips.

`spans`:41 · `holes`:48 · `ripple`:73 · `covered_gaps`:88 · `main`:108

### `tools/clip-cutter/scripts/concat_combos.py` — 95 lines
Build every combo by concatenating pre-rendered, captioned segment videos.

`concat_one`:32 · `out_path`:68 · `main`:74

### `tools/clip-cutter/scripts/concat_variants.py` — 309 lines
Build every hook x CTA variant from the compound clips exported once each.

`run`:55 · `probe`:60 · `discover`:78 · `matrix`:102 · `recode_target`:118 · `compatible`:133 · `concat_copy`:150 · `concat_recode`:170 · `verify`:211 · `main`:225

### `tools/clip-cutter/scripts/detect_takes.py` — 113 lines
Double-take remover — KEEP THE LAST clean take.

`whisperx_words`:26 · `norm`:46 · `last_take_start`:50 · `main`:82

### `tools/clip-cutter/scripts/edits.py` — 230 lines
edits.json — the human+auto edit overlay that survives re-planning.

`path_for`:27 · `load_edits`:31 · `save_edits`:42 · `next_cut_id`:46 · `append_cut`:57 · `project`:85 · `bake_src_cuts`:148 · `apply_removals_to_clips`:180 · `effective_plan`:210

### `tools/clip-cutter/scripts/explode_compounds.py` — 317 lines
Split a hand-made CapCut project into one standalone project per compound clip.

`project_dir`:52 · `compound_label`:73 · `placeholder_names`:89 · `media_of`:108 · `place_and_rewrite`:118 · `meta_entry`:167 · `cover_source`:182 · `write_solo`:197 · `explode`:234 · `main`:283

### `tools/clip-cutter/scripts/export_capcut.py` — 1369 lines
Export a caption-ugc edit as a CapCut project, for manual revision by an editor.

`gid`:50 · `us`:54 · `newest_template`:61 · `place_media`:113 · `describe_media`:142 · `make_cover`:153 · `cover_source`:178 · `as_shot`:205 · `pick_video_template`:235 · `pick_text_template`:262 · `template_token`:292 · `clone_extras`:323 · `build_text_content`:342 · `make_caption`:360 · `house_layout`:397 · `check_caption_widths`:422 · `make_headline`:459 · `wrap_headline`:485 · `build_timeline`:502 · `make_compound`:612 · `collect_media`:669 · `write_project`:689 · `meta_path`:746 · `reclaim`:765 · `register`:793 · `main`:847

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

### `tools/clip-cutter/scripts/plan_creative.py` — 258 lines
Plan a full creative — a PURE function of (config, clip probes).

`load_probe_cache`:42 · `probe_clip`:53 · `index_folder`:76 · `clip_file`:96 · `main`:119

### `tools/clip-cutter/scripts/plan_io.py` — 100 lines
The plan.json contract — one owner for the schema and the segments.ts emitter.

`seg_recipe`:21 · `segment_spans`:34 · `total_frames`:44 · `write_segments_ts`:48 · `write_plan`:57 · `load_plan`:61 · `validate_plan`:66

### `tools/clip-cutter/scripts/portable.py` — 595 lines
Where everything is, on whatever machine this is running on.

`ffmpeg`:111 · `ffprobe`:122 · `caption_tool`:131 · `cropper`:136 · `whisperx_python`:142 · `no_window_kwargs`:162 · `concat_line`:167 · `draft_file`:213 · `draft_file_name`:226 · `capcut_projects`:266 · `capcut_app`:338 · `capcut_font`:443 · `capcut_installed`:487 · `reset_cache`:493 · `capcut_template_count`:510 · `preflight`:520 · `missing`:561 · `require`:566

### `tools/clip-cutter/scripts/report.py` — 306 lines
Build manifest.json + review.tsv + a short review.md.

`font_spec_or_none`:45 · `measure`:54 · `flags_for`:64 · `main`:96 · `derive_manual`:271

### `tools/clip-cutter/scripts/run_clip_cutter.py` — 335 lines
One command behind Mariposa Studio's Clip Cutter: config.json -> CapCut project.

`say`:60 · `step`:68 · `run`:74 · `config_shape`:120 · `unprobed`:140 · `bytes_to_copy`:176 · `legs`:197 · `main`:228

### `tools/clip-cutter/scripts/run_creative.py` — 110 lines
One-command entry point: scaffold a project if needed, then hand off to build.py.

`sh`:31 · `scaffold`:36 · `main`:83

### `tools/clip-cutter/scripts/selftest.py` — 247 lines
Fast, read-only assertions against a built fixture. No renders, no mutation.

`check`:25

### `tools/clip-cutter/scripts/split_strip.py` — 202 lines
Cut a strip export back into the compounds it was built from.

`run`:38 · `probe`:43 · `check`:66 · `cut_all`:81 · `main`:129

### `tools/clip-cutter/scripts/srt.py` — 392 lines
SRT parsing / serialising / retiming — the one implementation.

`parse_srt`:18 · `load_srt`:35 · `fmt_ms`:40 · `dump_srt`:48 · `remap_cues`:55 · `rewrap`:90 · `partially_cut`:126 · `split_wide_cues`:151 · `align_cues_to_boundaries`:224 · `split_deep_cues`:328

### `tools/clip-cutter/scripts/srt2ass.py` — 204 lines
SRT -> ASS, reproducing template/src/caption-style.ts exactly.

`esc_filter_path`:39 · `ms_to_ass`:44 · `line_y`:59 · `measure_line`:67 · `ass_alpha`:98 · `build_ass`:103 · `srt_file_to_ass`:163 · `verify_font`:172

### `tools/clip-cutter/scripts/state.py` — 138 lines
state.json — the build's memory, plus locking and orphan pruning.

`path_for`:27 · `empty`:31 · `load_state`:36 · `save_state`:52 · `Lock`:56 · `find_orphans`:99 · `prune`:126 · `stamp`:137

### `tools/clip-cutter/scripts/steps.py` — 393 lines
Executors: one run_<kind>() per node kind. Every one writes <out>.part then os.replace()s it, so a kill -9 never leaves a half-written artifact adopted.

`sh`:47 · `run_probe`:67 · `run_proxy`:80 · `run_plan`:105 · `run_bundle`:112 · `run_wav`:126 · `run_srt`:134 · `clip_bounds_ms`:148 · `write_render_inputs`:158 · `write_srts_ts`:203 · `run_clean_batch`:218 · `run_cut`:245 · `run_ass`:297 · `run_burn`:334 · `run_combo`:367 · `run_crop`:382 · `run_report`:391

### `tools/clip-cutter/scripts/strip_compounds.py` — 225 lines
Lay every compound of a project end to end, so ONE export renders them all.

`sort_key`:43 · `outer_segments`:56 · `main_track`:68 · `copy_subdrafts`:77 · `build`:103 · `main`:186

### `tools/clip-cutter/scripts/tighten_audio.py` — 212 lines
Shorten every pause in a voiceover, without touching a word.

`detect`:38 · `phrases`:55 · `keeps`:73 · `render`:110 · `audio_props`:133 · `main`:146

### `tools/clip-cutter/scripts/tighten_gaps.py` — 187 lines
Auto dead-air removal — acoustic detection, word-timing guard rail.

`load_words`:55 · `find_holds`:68 · `propose_cuts`:100 · `main`:145

### `tools/extract-frame/extract_last_frame.py` — 134 lines
Extract frames from a video.

`extract`:48

### `tools/flow-cropper/crop.py` — 1454 lines
Flow Cropper — 9:16 → 4:5 batch crop + smart rename.

`normalize_creator`:91 · `safe_print`:104 · `rename_with_retry`:121 · `find_ffmpeg`:162 · `detect_creative_id`:181 · `select_encoder`:229 · `Probe`:244 · `probe`:257 · `crop_to_4x5`:399 · `encode_prior`:443 · `Reporter`:454 · `Reporter.emit`:464 · `Reporter.enter`:473 · `Reporter.done`:478 · `Reporter.skip`:482 · `Reporter.restart`:486 · `Reporter.frac`:490 · `normalize_creative_id`:503 · `fs_safe`:523 · `creative_name`:537 · `simple_name`:550 · `Clip`:579 · `UnitPlan`:594 · `UnitPlan.renames`:606 · `plan_unit`:615 · `RunState`:665 · `RunState.probe_all`:685 · `RunState.legs`:695 · `RunState.announce`:721 · `RunState.to_software`:724 · `process_unit`:743 · `NothingToDo`:836 · `plan_units`:918 · `nothing_found`:964 · `adopt_loose`:971 · `run`:1023 · `undo_last`:1077 · `pick_folder`:1150 · `ask_text`:1168 · `ask_choice`:1191 · `alert`:1234 · `interactive`:1262 · `run_with`:1305 · `main`:1381
