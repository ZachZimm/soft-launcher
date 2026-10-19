use soft_launcher::{
    Engine, ExecuteRequest,
    catalogue::{self, Store},
    collectors::{self, Collectors},
    confirmation::{Confirmations, TTL},
    exec::{self, ExecArgs, Plan},
    process::{CommandSpec, Runner, SystemRunner},
};
use std::{
    fs,
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
    time::{Duration, Instant, SystemTime},
};
const CATALOGUE: &str = include_str!("../../catalogue.toml");
#[derive(Default)]
struct FakeRunner {
    calls: Mutex<Vec<CommandSpec>>,
    window: Mutex<String>,
    clipboard: Mutex<Option<Vec<u8>>>,
    provider_reply: Mutex<Option<Vec<u8>>>,
}
impl Runner for FakeRunner {
    fn run(&self, c: &CommandSpec) -> Result<Vec<u8>, String> {
        self.calls.lock().unwrap().push(c.clone());
        Ok(match c.program.as_str() {
            "hyprctl" if c.args.first().is_some_and(|a| a == "dispatch") => b"ok\n".to_vec(),
            "hyprctl" => self.window.lock().unwrap().as_bytes().to_vec(),
            "wl-paste" if c.args == ["--list-types"] => b"text/plain\n".to_vec(),
            "wl-paste" => self
                .clipboard
                .lock()
                .unwrap()
                .clone()
                .unwrap_or_else(|| b"private clipboard content".to_vec()),
            "nmcli" if c.args == ["radio", "wifi"] => b"enabled\n".to_vec(),
            "python3" if c.args.last().is_some_and(|a| a == "--capabilities") => {
                br#"{"version":1,"destination":"hermes-desktop"}"#.to_vec()
            }
            "python3" => self
                .provider_reply
                .lock()
                .unwrap()
                .clone()
                .unwrap_or_else(|| br#"{"version":1,"destination":"hermes-desktop","requestId":"12345678-1234-4234-8234-123456789abc","status":"accepted"}"#.to_vec()),
            "ssh" => self
                .provider_reply
                .lock()
                .unwrap()
                .clone()
                .unwrap_or_else(|| br#"{"ok":true,"text":"Synthetic result"}"#.to_vec()),
            _ => Vec::new(),
        })
    }
}

#[test]
fn hermes_is_typed_only_and_confirmation_precedes_every_handoff() {
    let (dir, path, runner) = fixture();
    let engine = Engine::with_runner(path, dir.path().into(), runner.clone()).unwrap();
    let cat = engine.state("").unwrap().catalogue;
    let action = cat
        .actions
        .iter()
        .find(|a| a.id == "send_to_agent")
        .unwrap();
    assert!(action.requires_confirmation);
    assert!(!action.needs_text);
    runner.calls.lock().unwrap().clear();
    let args = ExecArgs {
        input: Some("  /Reply only hello. Literal $(never-run); café\nsecond line.  ".into()),
        ..Default::default()
    };
    let first = engine
        .execute(request("send_to_agent", args.clone(), None))
        .unwrap()
        .0;
    assert_eq!(first.status, "confirmationRequired");
    assert!(runner.calls.lock().unwrap().is_empty());
    let token = first.confirmation_token.unwrap();
    let changed = ExecArgs {
        input: Some("different typed request".into()),
        ..Default::default()
    };
    assert!(
        engine
            .execute(request("send_to_agent", changed, Some(token.clone())))
            .is_err()
    );
    let first = engine
        .execute(request("send_to_agent", args.clone(), None))
        .unwrap()
        .0;
    let token = first.confirmation_token.unwrap();
    let result = engine
        .execute(request("send_to_agent", args.clone(), Some(token.clone())))
        .unwrap()
        .0;
    assert_eq!(result.message, "Sent to Hermes Desktop");
    assert!(result.text.is_none());
    assert!(
        engine
            .execute(request("send_to_agent", args.clone(), Some(token)))
            .is_err()
    );
    let calls = runner.calls.lock().unwrap();
    assert_eq!(calls.len(), 2);
    assert_eq!(calls[0].program, "python3");
    assert_eq!(calls[0].args.last().unwrap(), "--capabilities");
    assert!(calls[0].stdin.is_none());
    assert_eq!(calls[0].timeout_ms + calls[1].timeout_ms, 50_000);
    let payload: serde_json::Value =
        serde_json::from_slice(calls[1].stdin.as_ref().unwrap()).unwrap();
    assert_eq!(payload, serde_json::json!({"text":args.input.unwrap()}));
    assert!(
        calls
            .iter()
            .all(|call| call.args.iter().all(|s| !s.contains("never-run")))
    );
}

#[test]
fn hermes_rejects_wrong_source_preview_bad_text_and_adapter_failures() {
    for invalid in ["", " ", "x\0y", &"é".repeat(2049)] {
        let (dir, path, runner) = fixture();
        let engine = Engine::with_runner(path, dir.path().into(), runner.clone()).unwrap();
        assert!(
            engine
                .execute(request(
                    "send_to_agent",
                    ExecArgs {
                        input: Some(invalid.into()),
                        ..Default::default()
                    },
                    None
                ))
                .is_err()
        );
        assert!(runner.calls.lock().unwrap().is_empty());
    }
    assert!(
        catalogue::parse(
            "[[action]]\nid='agent'\ndescription='x'\nexec={kind='hermes',arg='clipboard'}",
            1
        )
        .is_err()
    );
    let (dir, path, runner) = fixture();
    let engine = Engine::with_runner(path, dir.path().into(), runner.clone()).unwrap();
    assert!(
        engine
            .preview(soft_launcher::PreviewRequest {
                action_id: "send_to_agent".into(),
                args: Default::default()
            })
            .is_err()
    );
    let args = ExecArgs {
        input: Some("public task".into()),
        ..Default::default()
    };
    let first = engine
        .execute(request("send_to_agent", args.clone(), None))
        .unwrap()
        .0;
    *runner.provider_reply.lock().unwrap() =
        Some(br#"{"status":"error","error":"desktop"}"#.to_vec());
    assert_eq!(
        engine
            .execute(request("send_to_agent", args, first.confirmation_token))
            .err()
            .unwrap(),
        "Hermes Desktop unavailable"
    );
}

#[test]
fn desktop_probe_blocks_legacy_terminal_adapter_and_uncertain_outcomes_do_not_resend() {
    struct LegacyAdapter(Mutex<Vec<CommandSpec>>);
    impl Runner for LegacyAdapter {
        fn run(&self, spec: &CommandSpec) -> Result<Vec<u8>, String> {
            self.0.lock().unwrap().push(spec.clone());
            Ok(br#"{"status":"accepted"}"#.to_vec())
        }
    }
    let legacy = LegacyAdapter(Mutex::new(Vec::new()));
    let hermes = exec::hermes::Hermes::new("/inert/legacy-adapter.py".into());
    assert!(
        hermes
            .send(&legacy, "Public exact typed text")
            .unwrap_err()
            .contains("unsupported")
    );
    let calls = legacy.0.lock().unwrap();
    assert_eq!(calls.len(), 1);
    assert!(calls[0].stdin.is_none());
    drop(calls);

    let (dir, path, runner) = fixture();
    let engine = Engine::with_runner(path, dir.path().into(), runner.clone()).unwrap();
    let args = ExecArgs {
        input: Some("Public once only".into()),
        ..Default::default()
    };
    let first = engine
        .execute(request("send_to_agent", args.clone(), None))
        .unwrap()
        .0;
    *runner.provider_reply.lock().unwrap() = Some(br#"{"status":"error","error":"timeout","requestId":"12345678-1234-4234-8234-123456789abc"}"#.to_vec());
    let token = first.confirmation_token.unwrap();
    let error = engine
        .execute(request("send_to_agent", args.clone(), Some(token.clone())))
        .err()
        .unwrap();
    assert!(error.contains("unconfirmed"));
    assert!(error.contains("12345678-1234-4234-8234-123456789abc"));
    assert!(error.contains("check Desktop before sending again"));
    assert!(
        engine
            .execute(request("send_to_agent", args, Some(token)))
            .is_err()
    );
    assert_eq!(
        runner
            .calls
            .lock()
            .unwrap()
            .iter()
            .filter(|call| call.program == "python3" && call.stdin.is_some())
            .count(),
        1
    );
}

#[test]
fn codex_execution_and_teaching_are_bounded_and_proposals_do_not_write() {
    let (dir, path, runner) = fixture();
    *runner.window.lock().unwrap() = r#"{"class":"code","title":"Public document"}"#.into();
    *runner.clipboard.lock().unwrap() = Some(b"Public library hours: nine to five".to_vec());
    let original = fs::read(&path).unwrap();
    let engine = Engine::with_backends(
        path.clone(),
        dir.path().into(),
        runner.clone(),
        exec::codex::Codex::new(
            "buildbox".into(),
            "/safe/bridge.py".into(),
            "gpt-6.1-sol".into(),
        ),
        exec::hermes::Hermes::from_env(dir.path()),
    )
    .unwrap();
    let result = engine
        .execute(request("summarise_clipboard", ExecArgs::default(), None))
        .unwrap()
        .0;
    assert_eq!(result.text.as_deref(), Some("Synthetic result"));
    let generated = "[[action]]\nid='library_search'\ndescription='Search library opening hours'\nexec={kind='builtin',name='web_search',arg='input'}";
    *runner.provider_reply.lock().unwrap() =
        Some(serde_json::to_vec(&serde_json::json!({"ok":true,"text":generated})).unwrap());
    let proposal = engine
        .propose(vec!["public library opening hours".into()])
        .unwrap();
    assert!(proposal.toml.contains("library_search"));
    assert_eq!(fs::read(path).unwrap(), original);
    let calls = runner.calls.lock().unwrap();
    let texts: Vec<_> = calls.iter().filter(|c| c.program == "ssh").collect();
    assert_eq!(texts.len(), 2);
    assert!(texts.iter().all(|c| {
        c.args
            .iter()
            .all(|a| !a.contains("Bearer") && !a.contains("Public"))
    }));
    let body: serde_json::Value = serde_json::from_slice(texts[1].stdin.as_ref().unwrap()).unwrap();
    assert!(
        body["system"]
            .as_str()
            .unwrap()
            .contains("Do not propose shell or Hermes")
    );
    drop(calls);
    *runner.provider_reply.lock().unwrap() = Some(br#"{"ok":false,"error":"incomplete"}"#.to_vec());
    assert!(
        engine
            .execute(request("translate_clipboard", ExecArgs::default(), None))
            .is_err()
    );
}
fn fixture() -> (tempfile::TempDir, PathBuf, Arc<FakeRunner>) {
    let dir = tempfile::tempdir().unwrap();
    let catalogue = dir.path().join("catalogue.toml");
    fs::write(&catalogue, CATALOGUE).unwrap();
    (dir, catalogue, Arc::new(FakeRunner::default()))
}
fn request(id: &str, args: ExecArgs, token: Option<String>) -> ExecuteRequest {
    ExecuteRequest {
        action_id: id.into(),
        args,
        confirmation_token: token,
        expected_app: false,
    }
}
fn action(id: &str) -> catalogue::Action {
    catalogue::parse(CATALOGUE, 1)
        .unwrap()
        .actions
        .into_iter()
        .find(|a| a.id == id)
        .unwrap()
}
fn set_mtime(path: &Path, seconds: u64) {
    fs::OpenOptions::new()
        .write(true)
        .open(path)
        .unwrap()
        .set_times(
            fs::FileTimes::new()
                .set_modified(SystemTime::UNIX_EPOCH + Duration::from_secs(seconds)),
        )
        .unwrap();
}
#[test]
fn complete_catalogue_and_camel_case_wire() {
    let c = catalogue::parse(CATALOGUE, 7).unwrap();
    assert_eq!(c.actions.len(), 31);
    assert!(
        c.actions
            .iter()
            .find(|a| a.id == "run_shell")
            .unwrap()
            .requires_confirmation
    );
    let wire = serde_json::to_value(c).unwrap();
    assert_eq!(wire["generation"], 7);
    assert!(wire["actions"][0].get("needsText").is_some());
    assert!(wire["actions"][0].get("needs_text").is_none());
}
#[test]
fn catalogue_rejects_duplicates_unknown_executors_and_commands() {
    assert!(catalogue::parse(&format!("{CATALOGUE}\n{}", CATALOGUE), 1).is_err());
    assert!(
        catalogue::parse(
            &CATALOGUE.replace("kind = \"app\"", "kind = \"arbitrary\""),
            1
        )
        .is_err()
    );
    assert!(catalogue::parse("[[action]]\nid='evil'\ndescription='x'\nexec={kind='shell',arg='input',command='rm -rf /'}",1).is_err());
    assert!(
        catalogue::parse(
            "[[action]]\nid='evil'\ndescription='x'\nexec={kind='builtin',name='rm',arg='input'}",
            1
        )
        .is_err()
    );
}
#[test]
fn reload_keeps_last_good_then_advances_generation() {
    let (_dir, path, _) = fixture();
    let mut store = Store::open(path.clone()).unwrap();
    fs::write(&path, "invalid = [").unwrap();
    assert!(store.reload().is_err());
    assert_eq!(store.catalogue.generation, 1);
    assert_eq!(store.catalogue.actions.len(), 31);
    fs::write(&path, format!("{CATALOGUE}\n# changed")).unwrap();
    assert!(store.reload().unwrap());
    assert_eq!(store.catalogue.generation, 2);
    assert!(!store.reload().unwrap());
}
#[test]
fn every_executor_constructs_without_real_side_effects() {
    let (dir, _, runner) = fixture();
    let home = dir.path();
    let file = home.join("a; touch PWNED.pdf");
    fs::write(&file, "x").unwrap();
    let dest = home.join("dest");
    fs::create_dir(&dest).unwrap();
    fs::create_dir(home.join("notes")).unwrap();
    let app = collectors::apps::parse_desktop(
        "test.desktop",
        "[Desktop Entry]\nType=Application\nName=Test\nExec=test-app \"a; touch PWNED\" %U\n",
        Path::new("/test.desktop"),
    )
    .unwrap();
    *runner.window.lock().unwrap() =
        r#"{"class":"code","title":"Project","address":"0xabc"}"#.into();
    let collectors = Collectors::with_apps(home.into(), runner.clone(), vec![app]);
    let args = ExecArgs {
        input: Some("literal $(touch PWNED)".into()),
        file: Some(file.to_string_lossy().into()),
        app_id: Some("test.desktop".into()),
        url: Some("https://example.org/?q=a;b".into()),
        expr: Some("(2+3)*4".into()),
        text: Some("text ; $(touch PWNED)".into()),
        destination: Some(dest.to_string_lossy().into()),
        workspace: Some(2),
        process_name: Some("safe-name".into()),
        host: Some("user@example.org".into()),
        seconds: Some(60),
        value: Some(1.),
        from_unit: Some("km".into()),
        to_unit: Some("m".into()),
    };
    for action in catalogue::parse(CATALOGUE, 1).unwrap().actions {
        let mut args = args.clone();
        if action.id == "rename_file" {
            args.destination = Some("renamed.pdf".into());
        }
        let plan = exec::build(&action, &args, &collectors)
            .unwrap_or_else(|e| panic!("{}: {e}", action.id));
        match (&*action.id, plan) {
            ("run_shell", Plan::Command(c)) => {
                assert_eq!(c.program, "/bin/sh");
                assert_eq!(c.args, ["-c", "literal $(touch PWNED)"]);
            }
            ("open_app", Plan::Command(c)) => {
                assert_eq!(c.program, "test-app");
                assert_eq!(c.args, ["a; touch PWNED"]);
            }
            ("open_file" | "open_recent", Plan::Command(c)) => {
                assert_eq!(c.program, "xdg-open");
                assert_eq!(c.args, [file.to_string_lossy()]);
            }
            ("copy_result", Plan::Command(c)) => {
                assert_eq!(c.stdin.unwrap(), b"text ; $(touch PWNED)")
            }
            ("calc", Plan::Text(s)) => assert_eq!(s, "20"),
            ("set_timer", Plan::Timer(s)) => assert_eq!(s, 60),
            ("other", Plan::Noop) => {}
            (_, _) => {}
        }
    }
    assert!(!home.join("PWNED").exists());
    assert!(
        runner
            .calls
            .lock()
            .unwrap()
            .iter()
            .all(|c| ["hyprctl", "wl-paste"].contains(&c.program.as_str()))
    );
}
#[test]
fn shell_confirmation_cannot_be_bypassed_and_is_single_use() {
    let (dir, path, runner) = fixture();
    let engine = Engine::with_runner(path, dir.path().into(), runner.clone()).unwrap();
    let args = ExecArgs {
        input: Some("printf deliberate".into()),
        ..Default::default()
    };
    let (first, _) = engine
        .execute(request("run_shell", args.clone(), None))
        .unwrap();
    assert_eq!(first.status, "confirmationRequired");
    assert!(runner.calls.lock().unwrap().is_empty());
    assert!(
        engine
            .execute(request("run_shell", args.clone(), Some("fake".into())))
            .is_err()
    );
    let token = first.confirmation_token.unwrap();
    let (done, _) = engine
        .execute(request("run_shell", args.clone(), Some(token.clone())))
        .unwrap();
    assert_eq!(done.status, "executed");
    assert_eq!(runner.calls.lock().unwrap().len(), 1);
    assert!(
        engine
            .execute(request("run_shell", args, Some(token)))
            .is_err()
    );
    assert!(
        serde_json::from_str::<ExecuteRequest>(
            r#"{"actionId":"run_shell","args":{"input":"id"},"confirmed":true}"#
        )
        .is_err()
    );
}
#[test]
fn confirmations_bind_args_ids_generation_and_expiry() {
    let args = ExecArgs {
        input: Some("one".into()),
        ..Default::default()
    };
    let changed = ExecArgs {
        input: Some("two".into()),
        ..Default::default()
    };
    let now = Instant::now();
    let mut c = Confirmations::default();
    for (generation, id, a, time) in [
        (2, "shell", args.clone(), now),
        (1, "other", args.clone(), now),
        (1, "shell", changed, now),
        (1, "shell", args.clone(), now + TTL),
    ] {
        let token = c.issue(1, "shell", &args, now).unwrap();
        assert!(c.consume(&token, generation, id, &a, time).is_err());
        assert!(c.consume(&token, 1, "shell", &args, now).is_err());
    }
}
#[test]
fn loaded_ids_and_generation_are_revalidated_on_execution() {
    let (dir, path, runner) = fixture();
    let engine = Engine::with_runner(path.clone(), dir.path().into(), runner.clone()).unwrap();
    assert!(
        engine
            .execute(request("invented_by_model", ExecArgs::default(), None))
            .is_err()
    );
    let args = ExecArgs {
        input: Some("printf x".into()),
        ..Default::default()
    };
    let (first, _) = engine
        .execute(request("run_shell", args.clone(), None))
        .unwrap();
    fs::write(&path, format!("{CATALOGUE}\n# changed")).unwrap();
    engine.reload().unwrap();
    assert!(
        engine
            .execute(request("run_shell", args, first.confirmation_token))
            .is_err()
    );
    assert!(runner.calls.lock().unwrap().is_empty());
}
#[test]
fn teaching_registers_without_writes_then_explicit_review_saves_once() {
    let (dir, path, runner) = fixture();
    let engine = Engine::with_runner(path.clone(), dir.path().into(), runner).unwrap();
    let draft = "[[action]]\nid='lookup_recipe'\ndescription='Search for a recipe'\nexec={kind='builtin',name='web_search',arg='input'}";
    let proposal = engine.register_proposal(draft).unwrap();
    assert_eq!(fs::read_to_string(&path).unwrap(), CATALOGUE);
    assert!(engine.save("invented", draft).is_err());
    let saved = engine
        .save(
            &proposal.proposal_id,
            &draft.replace("a recipe", "a recipe safely"),
        )
        .unwrap();
    assert_eq!(saved.actions.len(), 32);
    assert_eq!(saved.generation, 2);
    assert!(engine.save(&proposal.proposal_id, draft).is_err());
}
#[test]
fn proposal_rejects_model_programs_and_external_edit_is_preserved() {
    let (dir, path, runner) = fixture();
    let engine = Engine::with_runner(path.clone(), dir.path().into(), runner).unwrap();
    assert!(engine.register_proposal("[[action]]\nid='evil'\ndescription='x'\nexec={kind='builtin',name='exec',arg='input'}").is_err());
    let draft = "[[action]]\nid='new_search'\ndescription='Search'\nexec={kind='builtin',name='web_search',arg='input'}";
    let p = engine.register_proposal(draft).unwrap();
    let edited = format!("{CATALOGUE}\n# human edited");
    fs::write(&path, &edited).unwrap();
    assert!(engine.save(&p.proposal_id, draft).is_err());
    assert_eq!(fs::read_to_string(path).unwrap(), edited);
}
#[test]
fn parser_clipboard_caps_unicode_detects_mime_and_redacts_private() {
    let data = "é".repeat(400);
    let c = collectors::clipboard::parse(b"text/plain\n", data.as_bytes(), false);
    assert_eq!(c.head.chars().count(), 200);
    assert_eq!(
        collectors::clipboard::parse(b"text/plain", b"https://example.org", false).kind,
        "url"
    );
    assert_eq!(
        collectors::clipboard::parse(b"image/png", b"secret", false).kind,
        "image"
    );
    let c = collectors::clipboard::parse(b"text/plain", b"secret", true);
    assert_eq!(c.kind, "private");
    assert!(c.head.is_empty());
}
#[test]
fn original_private_window_survives_overlay_focus() {
    let (dir, _, runner) = fixture();
    *runner.window.lock().unwrap() =
        r#"{"class":"firefox","title":"My bank password account","address":"0xabc"}"#.into();
    let c = Collectors::with_apps(dir.path().into(), runner.clone(), Vec::new());
    c.capture_foreground();
    c.set_overlay_active(true);
    *runner.window.lock().unwrap() =
        r#"{"class":"soft-launcher","title":"soft-launcher","address":"0xdef"}"#.into();
    let state = c.snapshot("pdf");
    let window = state.window.unwrap();
    assert_eq!(window.class, "firefox");
    assert!(window.title.starts_with("private:"));
    assert!(!window.title.contains("password"));
    assert_eq!(state.clipboard.kind, "private");
    assert!(state.clipboard.head.is_empty());
    assert!(!runner.calls.lock().unwrap().iter().any(|c| c.program=="wl-paste" && c.args.first().is_some_and(|s| s=="--no-newline")));
    c.set_overlay_active(false);
    let state = c.snapshot("");
    assert_eq!(state.clipboard.kind, "private");
}
#[test]
fn collector_context_cache_avoids_repeated_process_calls() {
    let (dir, _, runner) = fixture();
    *runner.window.lock().unwrap() = r#"{"class":"code","title":"Project"}"#.into();
    let c = Collectors::with_apps(dir.path().into(), runner.clone(), Vec::new());
    c.snapshot("one");
    let n = runner.calls.lock().unwrap().len();
    c.snapshot("two");
    assert_eq!(runner.calls.lock().unwrap().len(), n);
}
#[test]
fn desktop_parser_handles_quoting_codes_and_visibility() {
    let text = "[Desktop Entry]\nType=Application\nName=My App\nIcon=app-icon\nExec=/usr/bin/example \"space arg\" %c %i %f %%\nTerminal=true\n";
    let app = collectors::apps::parse_desktop("x", text, Path::new("/x.desktop")).unwrap();
    assert_eq!(
        app.argv,
        [
            "/usr/bin/example",
            "space arg",
            "My App",
            "--icon",
            "app-icon",
            "%"
        ]
    );
    assert!(app.terminal);
    for suffix in [
        "Hidden=true",
        "NoDisplay=true",
        "OnlyShowIn=GNOME;",
        "NotShowIn=Hyprland;",
    ] {
        assert!(
            collectors::apps::parse_desktop("x", &format!("{text}{suffix}\n"), Path::new("/x"))
                .is_none()
        );
    }
    assert!(collectors::apps::tokenize("app \"broken").is_none());
    assert!(
        collectors::apps::parse_desktop("x", &text.replace("%f", "%Z"), Path::new("/x")).is_none()
    );
}
#[test]
fn recent_parser_sorts_decodes_and_excludes_sensitive_and_remote() {
    let home = Path::new("/home/test");
    let text = r#"<xbel><bookmark href="file:///home/test/old.pdf" visited="2026-01-01"/><bookmark href="file:///home/test/new%20file.pdf" visited="2026-10-17"/><bookmark href="file:///home/test/lab/private.pdf" visited="2026-10-18"/><bookmark href="https://example.org/a"/><bookmark href="file://remote/home/test/no"/></xbel>"#;
    let recent = collectors::recent::parse(text, home);
    assert_eq!(recent.len(), 2);
    assert_eq!(recent[0].name, "new file.pdf");
    assert!(collectors::recent::parse("broken", home).is_empty());
}
#[test]
fn file_walk_respects_ignore_and_private_locations() {
    let home = tempfile::tempdir().unwrap();
    for path in [
        "ok/report.pdf",
        "gold/skip.pdf",
        "lab/skip.pdf",
        "node_modules/skip.pdf",
        ".ssh/id_rsa",
        "ok/private.key",
        "ignored/skip.pdf",
    ] {
        let path = home.path().join(path);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, "x").unwrap();
    }
    fs::write(home.path().join(".gitignore"), "ignored/\n").unwrap();
    let paths = collectors::files::scan(home.path());
    assert_eq!(paths.len(), 2);
    assert!(paths.contains(&home.path().join("ok")));
    assert!(paths.contains(&home.path().join("ok/report.pdf")));
}
#[test]
fn natural_download_query_prefers_newest_pdf_and_exposes_mtime() {
    let home = tempfile::tempdir().unwrap();
    let downloads = home.path().join("Downloads");
    fs::create_dir(&downloads).unwrap();
    let paths = [
        downloads.join("old.pdf"),
        downloads.join("invoice-oct.pdf"),
        downloads.join("newer.txt"),
        home.path().join("elsewhere.pdf"),
    ];
    for (i, path) in paths.iter().enumerate() {
        fs::write(path, "x").unwrap();
        set_mtime(path, 100 + i as u64);
    }
    let found = collectors::files::fuzzy(&paths, "the pdf I just downloaded");
    assert_eq!(found.len(), 2);
    assert_eq!(found[0].name, "invoice-oct.pdf");
    assert_eq!(found[0].modified_at, Some(101000));
    assert_eq!(
        collectors::files::fuzzy(&paths, "inv oct")[0].name,
        "invoice-oct.pdf"
    );
    assert_eq!(
        collectors::files::fuzzy(&paths, "Downloads/inv")[0].name,
        "invoice-oct.pdf"
    );
}
#[test]
fn calc_convert_and_timer_reject_invalid_inputs() {
    assert_eq!(exec::builtin::calc("(2+3)^2").unwrap(), "25");
    for expr in ["system(id)", "1/0", "NaN", "2;id", ""] {
        assert!(exec::builtin::calc(expr).is_err());
    }
    assert_eq!(
        exec::builtin::convert(1., "km", "m").unwrap(),
        "1000.000000 m"
    );
    assert_eq!(exec::builtin::convert(32., "f", "c").unwrap(), "0.000000 c");
    assert!(exec::builtin::convert(1., "kg", "m").is_err());
    let (dir, _, runner) = fixture();
    let c = Collectors::with_apps(dir.path().into(), runner, Vec::new());
    for seconds in [0, 86401] {
        assert!(
            exec::build(
                &action("set_timer"),
                &ExecArgs {
                    seconds: Some(seconds),
                    ..Default::default()
                },
                &c
            )
            .is_err()
        );
    }
}
#[test]
fn normalized_open_url_dispatches_once_through_native_validation() {
    let (dir, path, runner) = fixture();
    let engine = Engine::with_runner(path, dir.path().into(), runner.clone()).unwrap();
    runner.calls.lock().unwrap().clear();
    let result = engine
        .execute(request(
            "open_url",
            ExecArgs {
                url: Some("https://openrouter.ai/".into()),
                ..Default::default()
            },
            None,
        ))
        .unwrap()
        .0;
    assert_eq!(result.status, "executed");
    let calls = runner.calls.lock().unwrap();
    assert_eq!(calls.len(), 1);
    assert_eq!(calls[0].program, "xdg-open");
    assert_eq!(calls[0].args, ["https://openrouter.ai/"]);
    assert!(calls[0].detached);
}

#[test]
fn malformed_urls_never_reach_runner() {
    let (dir, path, runner) = fixture();
    let engine = Engine::with_runner(path, dir.path().into(), runner.clone()).unwrap();
    runner.calls.lock().unwrap().clear();
    for value in [
        "openrouter.ai",
        "javascript:alert(1)",
        "file:///etc/passwd",
        "https://user:secret@example.org",
        "https:example.org",
        "https:///example.org",
        "https://example.org\\evil",
        "https://example.org/\nnext",
        "https://example.org/\u{1}",
        "https://-example.org",
        "https://example..org",
        "https://example.org:99999",
        "https://",
    ] {
        assert!(
            engine
                .execute(request(
                    "open_url",
                    ExecArgs {
                        url: Some(value.into()),
                        ..Default::default()
                    },
                    None
                ))
                .is_err(),
            "{value:?}"
        );
    }
    assert!(runner.calls.lock().unwrap().is_empty());
    for value in [
        "http://example.org:8080/path?x=1",
        "https://example.org/?q=why?",
        "https://localhost:3000/",
        "http://[::1]:8080/path",
    ] {
        assert_eq!(exec::xdg::url(value).unwrap(), value);
    }
}

#[test]
fn urls_files_and_arguments_reject_injection_routes() {
    for url in [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "https://user:pass@example.org",
        "--help",
    ] {
        assert!(exec::xdg::url(url).is_err());
    }
    let home = tempfile::tempdir().unwrap();
    assert!(exec::xdg::file("--help", home.path()).is_err());
    assert!(exec::xdg::file("/etc/passwd", home.path()).is_err());
    assert!(serde_json::from_str::<ExecArgs>(r#"{"command":"id"}"#).is_err());
    assert!(
        ExecArgs {
            input: Some("x\0y".into()),
            ..Default::default()
        }
        .validate()
        .is_err()
    );
    let (dir, _, runner) = fixture();
    let c = Collectors::with_apps(dir.path().into(), runner, Vec::new());
    for (id, args) in [
        (
            "kill_process",
            ExecArgs {
                process_name: Some("-9 .*".into()),
                ..Default::default()
            },
        ),
        (
            "ssh_to_host",
            ExecArgs {
                host: Some("-oProxyCommand=id".into()),
                ..Default::default()
            },
        ),
        (
            "switch_workspace",
            ExecArgs {
                workspace: Some(100),
                ..Default::default()
            },
        ),
        (
            "open_app",
            ExecArgs {
                app_id: Some("invented".into()),
                ..Default::default()
            },
        ),
    ] {
        assert!(exec::build(&action(id), &args, &c).is_err());
    }
}
#[test]
fn wifi_toggle_uses_injected_runner_and_picks_inverse_state() {
    let (dir, _, runner) = fixture();
    let c = Collectors::with_apps(dir.path().into(), runner.clone(), Vec::new());
    let p = exec::build(&action("toggle_wifi"), &ExecArgs::default(), &c).unwrap();
    exec::perform(
        &p,
        runner.as_ref(),
        &exec::codex::Codex::from_env(dir.path()),
        &exec::hermes::Hermes::from_env(dir.path()),
    )
    .unwrap();
    assert_eq!(
        runner.calls.lock().unwrap()[1].args,
        ["radio", "wifi", "off"]
    );
}
#[test]
fn text_client_uses_credential_free_ssh_stdin_and_requires_completed_reply() {
    #[derive(Default)]
    struct TextRunner(Mutex<Vec<CommandSpec>>);
    impl Runner for TextRunner {
        fn run(&self, c: &CommandSpec) -> Result<Vec<u8>, String> {
            self.0.lock().unwrap().push(c.clone());
            Ok(br#"{"ok":true,"text":"A bounded reply"}"#.to_vec())
        }
    }
    let runner = TextRunner::default();
    let client =
        exec::codex::Codex::new("lab".into(), "/safe/bridge.py".into(), "gpt-6.1-sol".into());
    assert_eq!(
        client
            .text(&runner, "summarize", "literal $(do-not-run)")
            .unwrap(),
        "A bounded reply"
    );
    let calls = runner.0.lock().unwrap();
    assert_eq!(calls.len(), 1);
    assert_eq!(calls[0].program, "ssh");
    assert!(calls[0].args.iter().any(|s| s == "BatchMode=yes"));
    assert!(
        !calls[0]
            .args
            .iter()
            .any(|s| s.contains("literal") || s.contains("Bearer"))
    );
    let body: serde_json::Value = serde_json::from_slice(calls[0].stdin.as_ref().unwrap()).unwrap();
    assert_eq!(body["input"], "literal $(do-not-run)");
    assert_eq!(body["model"], "gpt-6.1-sol");
    for (host, bridge) in [("", "/safe/bridge.py"), ("buildbox", "")] {
        let error = exec::codex::Codex::new(host.into(), bridge.into(), "x".into())
            .chat(&runner, "x", "x")
            .unwrap_err();
        assert!(error.contains("configure SOFT_LAUNCHER_CODEX_SSH_HOST"));
    }
    assert!(client.chat(&runner, "x", &"x".repeat(8193)).is_err());
    assert!(
        exec::codex::Codex::new("-bad".into(), "/safe/bridge.py".into(), "x".into())
            .chat(&runner, "x", "x")
            .is_err()
    );
    assert!(
        exec::codex::Codex::new("lab".into(), "/safe/$(bad)".into(), "x".into())
            .chat(&runner, "x", "x")
            .is_err()
    );
}
#[test]
fn system_runner_keeps_arguments_literal_and_caps_output() {
    let mut c = CommandSpec::new("/usr/bin/printf", ["%s", "$(touch NEVER);literal"]);
    c.output_limit = 8;
    let out = SystemRunner.run(&c).unwrap();
    assert_eq!(out, b"$(touch ");
}

#[test]
fn paste_uses_full_native_clipboard_and_changed_content_invalidates_confirmation() {
    let (dir, path, runner) = fixture();
    *runner.window.lock().unwrap() =
        r#"{"class":"code","title":"Document","address":"0xabc"}"#.into();
    *runner.clipboard.lock().unwrap() = Some("full clipboard text ".repeat(100).into_bytes());
    let engine = Engine::with_runner(path, dir.path().into(), runner.clone()).unwrap();
    engine.collectors.capture_foreground();
    engine.collectors.set_overlay_active(true);
    assert_eq!(
        engine
            .state("")
            .unwrap()
            .context
            .clipboard
            .head
            .chars()
            .count(),
        200
    );
    let p = exec::build(
        &action("paste_plain"),
        &ExecArgs::default(),
        &engine.collectors,
    )
    .unwrap();
    if let Plan::Commands(commands) = p {
        assert!(commands[0].stdin.as_ref().unwrap().len() > 200);
    } else {
        panic!("expected paste commands");
    }
    let (pending, _) = engine
        .execute(request("paste_plain", ExecArgs::default(), None))
        .unwrap();
    *runner.clipboard.lock().unwrap() = Some(b"changed after review".to_vec());
    assert!(
        engine
            .execute_with_hook(
                request(
                    "paste_plain",
                    ExecArgs::default(),
                    pending.confirmation_token
                ),
                |_| panic!("must reject before focus/dispatch")
            )
            .is_err()
    );
    assert!(
        !runner
            .calls
            .lock()
            .unwrap()
            .iter()
            .any(|c| c.program == "wtype" || c.program == "wl-copy")
    );
}
#[test]
fn focus_hook_runs_only_after_confirmation_and_before_input_dispatch() {
    let (dir, path, runner) = fixture();
    *runner.window.lock().unwrap() = r#"{"class":"code","title":"Doc","address":"0xabc"}"#.into();
    let engine = Engine::with_runner(path, dir.path().into(), runner.clone()).unwrap();
    let (pending, _) = engine
        .execute_with_hook(request("paste_plain", ExecArgs::default(), None), |_| {
            panic!("first call must not hide or run")
        })
        .unwrap();
    engine
        .execute_with_hook(
            request(
                "paste_plain",
                ExecArgs::default(),
                pending.confirmation_token,
            ),
            |_| {
                assert!(
                    !runner
                        .calls
                        .lock()
                        .unwrap()
                        .iter()
                        .any(|c| c.program == "wtype")
                );
                engine.collectors.focus_original()
            },
        )
        .unwrap();
    let calls = runner.calls.lock().unwrap();
    let focus = calls
        .iter()
        .position(|c| c.program == "hyprctl" && c.args.first().is_some_and(|s| s == "dispatch"))
        .unwrap();
    let input = calls.iter().position(|c| c.program == "wtype").unwrap();
    assert!(focus < input);
}
#[test]
fn all_dangerous_builtins_force_confirmation_in_validated_catalogue() {
    let source = CATALOGUE.replace(
        "exec = { kind = \"shell\", arg = \"input\" }",
        "requires_confirmation = false\nexec = { kind = \"shell\", arg = \"input\" }",
    );
    let catalogue = catalogue::parse(&source, 1).unwrap();
    for id in [
        "run_shell",
        "paste_plain",
        "rename_file",
        "move_to_folder",
        "kill_process",
        "toggle_wifi",
        "ssh_to_host",
    ] {
        assert!(
            catalogue
                .actions
                .iter()
                .find(|a| a.id == id)
                .unwrap()
                .requires_confirmation,
            "{id}"
        );
    }
}

#[test]
fn process_name_regex_metacharacters_are_literal() {
    let (dir, _, runner) = fixture();
    let c = Collectors::with_apps(dir.path().into(), runner, Vec::new());
    let p = exec::build(
        &action("kill_process"),
        &ExecArgs {
            process_name: Some("safe.app".into()),
            ..Default::default()
        },
        &c,
    )
    .unwrap();
    if let Plan::Command(command) = p {
        assert_eq!(command.args.last().unwrap(), "safe\\.app");
    } else {
        panic!("expected pkill command");
    }
}

#[test]
fn failed_foreground_capture_clears_old_public_context_and_focus() {
    let (dir, _, runner) = fixture();
    *runner.window.lock().unwrap() =
        r#"{"class":"code","title":"Public project","address":"0xabc"}"#.into();
    let c = Collectors::with_apps(dir.path().into(), runner.clone(), Vec::new());
    c.capture_foreground();
    c.set_overlay_active(true);
    assert_eq!(c.snapshot("").clipboard.kind, "text");
    *runner.window.lock().unwrap() = "not valid JSON".into();
    c.set_overlay_active(false);
    c.capture_foreground();
    c.set_overlay_active(true);
    let state = c.snapshot("");
    assert!(state.window.is_none());
    assert_eq!(state.clipboard.kind, "private");
    assert!(state.clipboard.head.is_empty());
    assert!(c.focus_original().is_err());
}
#[test]
fn natural_rename_move_queries_resolve_only_source_and_projects_include_directories() {
    let home = tempfile::tempdir().unwrap();
    let source = home.path().join("old.txt");
    fs::write(&source, "test").unwrap();
    let spaced = home.path().join("old file.txt");
    fs::write(&spaced, "test").unwrap();
    let project = home.path().join("my-project");
    fs::create_dir(&project).unwrap();
    let paths = collectors::files::scan(home.path());
    for input in [
        "rename old.txt to new.txt",
        "move old.txt into /tmp/somewhere",
    ] {
        assert_eq!(collectors::files::source_query(input), "old.txt");
        assert_eq!(
            collectors::files::fuzzy(&paths, input)[0].path,
            source.to_string_lossy()
        );
    }
    assert_eq!(
        collectors::files::fuzzy(&paths, "rename \"old file.txt\" as new.txt")[0].path,
        spaced.to_string_lossy()
    );
    let found = collectors::files::fuzzy(&paths, "open project my-project");
    assert_eq!(found[0].path, project.to_string_lossy());
    assert!(found[0].is_directory);
}
#[test]
fn text_actions_use_full_native_clipboard_and_refuse_private_or_oversized_text() {
    let (dir, _, runner) = fixture();
    *runner.window.lock().unwrap() = r#"{"class":"code","title":"Public document"}"#.into();
    *runner.clipboard.lock().unwrap() = Some("full native clipboard ".repeat(30).into_bytes());
    let c = Collectors::with_apps(dir.path().into(), runner.clone(), Vec::new());
    let args = ExecArgs {
        text: Some("preview only".into()),
        ..Default::default()
    };
    for id in ["summarise_clipboard", "translate_clipboard"] {
        if let Plan::Codex { text, .. } = exec::build(&action(id), &args, &c).unwrap() {
            assert!(text.len() > 200);
            assert!(text.starts_with("full native clipboard"));
        } else {
            panic!("expected text plan");
        }
    }
    *runner.clipboard.lock().unwrap() = Some(vec![b'x'; 8193]);
    assert!(exec::build(&action("summarise_clipboard"), &args, &c).is_err());
    *runner.window.lock().unwrap() = r#"{"class":"keepassxc","title":"Passwords"}"#.into();
    c.set_overlay_active(false);
    c.capture_foreground();
    c.set_overlay_active(true);
    assert!(exec::build(&action("summarise_clipboard"), &args, &c).is_err());
}
#[test]
fn catalogue_rejects_builtin_argument_source_mismatches() {
    for (name, wrong, right) in [
        ("calc", "input", "expr"),
        ("timer", "input", "seconds"),
        ("copy_result", "none", "text"),
        ("open_editor", "input", "file"),
    ] {
        let draft = |arg| {
            format!(
                "[[action]]\nid='test_action'\ndescription='Test action'\nexec={{kind='builtin',name='{name}',arg='{arg}'}}"
            )
        };
        assert!(catalogue::parse(&draft(wrong), 1).is_err(), "{name}");
        assert!(catalogue::parse(&draft(right), 1).is_ok(), "{name}");
    }
}

#[test]
fn source_query_uses_first_delimiter_and_original_unicode_boundaries() {
    for (input, expected) in [
        ("rename old.txt to how to use.txt", "old.txt"),
        ("move old.txt to /home/test/how to use", "old.txt"),
        ("rename İİ.txt to new.txt", "İİ.txt"),
        ("rename İİİİİİİİİ.txt to ééé.txt", "İİİİİİİİİ.txt"),
        ("rename old.txt   TO  how to use.txt", "old.txt"),
        ("rename \"old to use.txt\" to new.txt", "old to use.txt"),
    ] {
        assert_eq!(collectors::files::source_query(input), expected, "{input}");
    }
}

#[test]
fn hyprland_lua_dispatchers_validate_targets_and_reject_warnings() {
    let command = collectors::hyprland::focus_command("0xabc").unwrap();
    assert_eq!(command.args[0], "dispatch");
    assert!(command.args[1].contains("hl.get_window(\"address:0xabc\")"));
    assert!(command.args[1].contains("if not w then error"));
    assert!(command.args[1].contains("hl.dsp.focus({ window = w })"));
    for bad in ["0x", "0xabc; os.execute('id')", "0xzz", "activewindow"] {
        assert!(collectors::hyprland::focus_command(bad).is_err());
    }
    assert!(collectors::hyprland::dispatch_ok(b"ok\n").is_ok());
    assert!(collectors::hyprland::dispatch_ok(b"warning: Window is fullscreen").is_err());
    let (dir, _, runner) = fixture();
    let c = Collectors::with_apps(dir.path().into(), runner, Vec::new());
    if let Plan::Command(command) = exec::build(
        &action("switch_workspace"),
        &ExecArgs {
            workspace: Some(2),
            ..Default::default()
        },
        &c,
    )
    .unwrap()
    {
        assert_eq!(
            command.args,
            ["dispatch", "hl.dsp.focus({ workspace = 2 })"]
        );
    } else {
        panic!("expected workspace command");
    }
}

#[test]
fn preview_matches_calc_and_convert_execution_without_side_effects() {
    use soft_launcher::PreviewRequest;
    let (dir, path, runner) = fixture();
    let engine = Engine::with_runner(path.clone(), dir.path().into(), runner.clone()).unwrap();
    let original_catalogue = fs::read(&path).unwrap();
    for (id, args) in [
        (
            "calc",
            ExecArgs {
                expr: Some("2+2".into()),
                ..Default::default()
            },
        ),
        (
            "convert",
            ExecArgs {
                value: Some(1.),
                from_unit: Some("km".into()),
                to_unit: Some("m".into()),
                ..Default::default()
            },
        ),
    ] {
        let preview = engine
            .preview(PreviewRequest {
                action_id: id.into(),
                args: args.clone(),
            })
            .unwrap();
        let (executed, timer) = engine.execute(request(id, args, None)).unwrap();
        assert_eq!(Some(preview.text), executed.text);
        assert_eq!(preview.action_id, id);
        assert_eq!(timer, None);
    }
    assert!(runner.calls.lock().unwrap().is_empty());
    assert_eq!(fs::read(path).unwrap(), original_catalogue);
}

#[test]
fn preview_rejects_other_actions_rebound_executors_and_invalid_arguments() {
    use soft_launcher::PreviewRequest;
    let (dir, path, runner) = fixture();
    let engine = Engine::with_runner(path, dir.path().into(), runner.clone()).unwrap();
    for id in ["run_shell", "set_timer", "paste_plain", "other", "invented"] {
        assert!(
            engine
                .preview(PreviewRequest {
                    action_id: id.into(),
                    args: ExecArgs::default()
                })
                .is_err()
        );
    }
    for (id, args) in [
        (
            "calc",
            ExecArgs {
                expr: Some("1/0".into()),
                ..Default::default()
            },
        ),
        (
            "calc",
            ExecArgs {
                expr: Some("9".repeat(257)),
                ..Default::default()
            },
        ),
        (
            "convert",
            ExecArgs {
                value: Some(1.),
                from_unit: Some("kg".into()),
                to_unit: Some("m".into()),
                ..Default::default()
            },
        ),
    ] {
        let preview_error = engine
            .preview(PreviewRequest {
                action_id: id.into(),
                args: args.clone(),
            })
            .err()
            .unwrap();
        assert_eq!(
            preview_error,
            engine.execute(request(id, args, None)).err().unwrap()
        );
    }
    let mut rebound = action("calc");
    rebound.exec.kind = "shell".into();
    rebound.exec.name = None;
    assert!(
        exec::preview(
            &rebound,
            &ExecArgs {
                input: Some("touch nope".into()),
                ..Default::default()
            }
        )
        .is_err()
    );
    assert!(
        serde_json::from_str::<PreviewRequest>(
            r#"{"actionId":"calc","args":{"expr":"2+2"},"confirmationToken":"no"}"#
        )
        .is_err()
    );
    assert!(runner.calls.lock().unwrap().is_empty());
}

#[test]
fn installed_apps_apply_xdg_precedence_metadata_and_executable_validation() {
    use std::os::unix::fs::PermissionsExt;
    let dir = tempfile::tempdir().unwrap();
    let user = dir.path().join("user");
    let system = dir.path().join("system");
    for root in [&user, &system] {
        fs::create_dir_all(root.join("applications/nested")).unwrap();
    }
    let bin = dir.path().join("bin");
    fs::create_dir(&bin).unwrap();
    fs::write(bin.join("fixture-app"), "fixture, never executed").unwrap();
    fs::set_permissions(bin.join("fixture-app"), fs::Permissions::from_mode(0o755)).unwrap();
    let entry = "[Desktop Entry]\nType=Application\nName=Brave\nGenericName=Web Browser\nKeywords=Internet;Browse;\nExec=fixture-app %U\nIcon=brave-desktop\n";
    fs::write(system.join("applications/brave-browser.desktop"), entry).unwrap();
    fs::write(system.join("applications/masked.desktop"), entry).unwrap();
    fs::write(
        user.join("applications/masked.desktop"),
        "[Desktop Entry]\nHidden=true\n",
    )
    .unwrap();
    fs::write(system.join("applications/nodisplay.desktop"), entry).unwrap();
    fs::write(
        user.join("applications/nodisplay.desktop"),
        format!("{entry}NoDisplay=true\n"),
    )
    .unwrap();
    fs::write(
        system.join("applications/missing.desktop"),
        entry.replace("fixture-app %U", "missing-executable"),
    )
    .unwrap();
    fs::write(
        system.join("applications/tryexec.desktop"),
        format!("{entry}TryExec=missing-executable\n"),
    )
    .unwrap();
    fs::write(
        system.join("applications/nested/editor.desktop"),
        entry.replace("Name=Brave", "Name=Editor"),
    )
    .unwrap();
    let apps = collectors::apps::collect_roots(&[user.clone(), system.clone()], &[bin]);
    assert_eq!(
        apps.iter().map(|a| a.id.as_str()).collect::<Vec<_>>(),
        ["brave-browser.desktop", "nested-editor.desktop"]
    );
    let wire = serde_json::to_value(&apps[0]).unwrap();
    assert_eq!(wire["genericName"], "Web Browser");
    assert_eq!(wire["keywords"], serde_json::json!(["Internet", "Browse"]));
    assert!(wire.get("argv").is_none());
    assert!(wire.get("desktopPath").is_none());
    assert!(wire.get("iconKey").is_none());
    fs::write(
        user.join("applications/brave-browser.desktop"),
        entry.replace("Name=Brave", "Name=Personal Browser"),
    )
    .unwrap();
    let updated = collectors::apps::collect_roots(&[user, system], &[dir.path().join("bin")]);
    assert_eq!(
        updated
            .iter()
            .find(|a| a.id == "brave-browser.desktop")
            .unwrap()
            .name,
        "Personal Browser"
    );
}

#[test]
fn known_app_launch_rejects_removed_hidden_or_changed_desktop_entry() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().join("data");
    fs::create_dir_all(root.join("applications")).unwrap();
    let desktop = root.join("applications/fixture.desktop");
    let entry = "[Desktop Entry]\nType=Application\nName=Fixture\nExec=/bin/true\n";
    fs::write(&desktop, entry).unwrap();
    let apps = collectors::apps::collect_roots(&[root], &[]);
    assert_eq!(apps.len(), 1);
    let collectors =
        Collectors::with_apps(dir.path().into(), Arc::new(FakeRunner::default()), apps);
    let args = ExecArgs {
        app_id: Some("fixture.desktop".into()),
        ..Default::default()
    };
    assert!(exec::build(&action("open_app"), &args, &collectors).is_ok());
    fs::write(&desktop, entry.replace("/bin/true", "/bin/false")).unwrap();
    assert!(exec::build(&action("open_app"), &args, &collectors).is_err());
    fs::write(&desktop, format!("{entry}Hidden=true\n")).unwrap();
    assert!(exec::build(&action("open_app"), &args, &collectors).is_err());
    fs::remove_file(desktop).unwrap();
    assert!(exec::build(&action("open_app"), &args, &collectors).is_err());
}

#[test]
fn direct_app_expectation_rejects_guarded_and_rebound_catalogue_under_native_lock() {
    let (dir, path, runner) = fixture();
    let engine = Engine::with_runner(path.clone(), dir.path().into(), runner.clone()).unwrap();
    for replacement in [
        "exec = { kind = \"builtin\", name = \"volume_up\", arg = \"none\" }",
        "exec = { kind = \"shell\", arg = \"input\" }",
        "exec = { kind = \"app\", arg = \"app\" }\nrequires_confirmation = true",
    ] {
        let changed =
            CATALOGUE.replacen("exec = { kind = \"app\", arg = \"app\" }", replacement, 1);
        fs::write(&path, changed).unwrap();
        engine.reload().unwrap();
        let mut request = request(
            "open_app",
            ExecArgs {
                app_id: Some("invented.desktop".into()),
                input: Some("never execute".into()),
                ..Default::default()
            },
            None,
        );
        request.expected_app = true;
        let error = engine.execute(request).err().unwrap();
        assert!(
            error.contains("application opening is unavailable"),
            "{error}"
        );
    }
    assert!(
        !runner
            .calls
            .lock()
            .unwrap()
            .iter()
            .any(|c| c.program == "wpctl" || c.program == "/bin/sh")
    );
}

#[test]
fn app_icons_resolve_bounded_pngs_and_omit_invalid_or_external_payloads() {
    use base64::Engine as _;
    let dir = tempfile::tempdir().unwrap();
    let icon = dir.path().join("hicolor/128x128/apps/fixture.png");
    fs::create_dir_all(icon.parent().unwrap()).unwrap();
    let mut data = Vec::new();
    {
        let mut encoder = png::Encoder::new(&mut data, 128, 128);
        encoder.set_color(png::ColorType::Rgba);
        encoder.set_depth(png::BitDepth::Eight);
        encoder
            .write_header()
            .unwrap()
            .write_image_data(&vec![127; 128 * 128 * 4])
            .unwrap();
    }
    fs::write(&icon, data).unwrap();
    let mut app = collectors::apps::parse_desktop(
        "fixture.desktop",
        "[Desktop Entry]\nType=Application\nName=Fixture\nExec=/bin/true\nIcon=fixture\n",
        Path::new("/fixture.desktop"),
    )
    .unwrap();
    collectors::apps::attach_icons(std::slice::from_mut(&mut app), &[dir.path().into()]);
    let payload = app.icon.unwrap();
    let png = base64::engine::general_purpose::STANDARD
        .decode(payload.strip_prefix("data:image/png;base64,").unwrap())
        .unwrap();
    assert!(png.len() <= collectors::apps::MAX_ICON_BYTES);
    let reader = png::Decoder::new(std::io::Cursor::new(png))
        .read_info()
        .unwrap();
    assert_eq!((reader.info().width, reader.info().height), (64, 64));
    fs::write(&icon, "not a PNG, https://example.org").unwrap();
    assert!(collectors::apps::icon_png(&icon).is_none());
    fs::write(&icon, vec![0; 64 * 1024 + 1]).unwrap();
    assert!(collectors::apps::icon_png(&icon).is_none());
    let svg = dir.path().join("icon.svg");
    fs::write(&svg, "<svg/>").unwrap();
    assert!(collectors::apps::icon_png(&svg).is_none());
}

#[test]
fn app_icon_accepts_large_bounded_installed_png_before_downscaling() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("editor.png");
    let mut data = Vec::new();
    {
        let mut encoder = png::Encoder::new(&mut data, 1024, 1024);
        encoder.set_color(png::ColorType::Rgba);
        encoder.set_depth(png::BitDepth::Eight);
        encoder
            .write_header()
            .unwrap()
            .write_image_data(&vec![31; 1024 * 1024 * 4])
            .unwrap();
    }
    fs::write(&path, data).unwrap();
    let output = collectors::apps::icon_png(&path).unwrap();
    let reader = png::Decoder::new(std::io::Cursor::new(output))
        .read_info()
        .unwrap();
    assert_eq!((reader.info().width, reader.info().height), (64, 64));
}

#[cfg(feature = "desktop")]
#[test]
fn installed_svg_is_rasterized_to_png_without_external_references() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("files.svg");
    fs::write(&path, r##"<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128"><rect width="128" height="128" fill="#008080"/></svg>"##).unwrap();
    let output = collectors::apps::icon_svg(&path).unwrap();
    let reader = png::Decoder::new(std::io::Cursor::new(output))
        .read_info()
        .unwrap();
    assert_eq!((reader.info().width, reader.info().height), (64, 64));
    fs::write(
        &path,
        r#"<svg xmlns="http://www.w3.org/2000/svg"><image href="file:///private/file.png"/></svg>"#,
    )
    .unwrap();
    assert!(collectors::apps::icon_svg(&path).is_none());
    fs::write(&path, r#"<svg xmlns="http://www.w3.org/2000/svg"><style>@import 'https://example.org/icon.css';</style></svg>"#).unwrap();
    assert!(collectors::apps::icon_svg(&path).is_none());
}
