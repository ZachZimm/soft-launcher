//! Bounded public-text native integration. No GUI, agent tools, or catalogue save.
use soft_launcher::{
    Engine, ExecuteRequest, catalogue,
    exec::ExecArgs,
    process::{CommandSpec, Runner, SystemRunner},
};
use std::{fs, path::PathBuf, sync::Arc};

struct PublicRunner;
impl Runner for PublicRunner {
    fn run(&self, spec: &CommandSpec) -> Result<Vec<u8>, String> {
        match spec.program.as_str() {
            "hyprctl" => Ok(br#"{"class":"public-fixture","title":"Public validation"}"#.to_vec()),
            "wl-paste" if spec.args == ["--list-types"] => Ok(b"text/plain\n".to_vec()),
            "wl-paste" => Ok("La biblioteca abre a las nueve y cierra a las cinco. Los domingos permanece cerrada.".as_bytes().to_vec()),
            "ssh" => SystemRunner.run(spec),
            _ => Err("validation refuses tools and side effects".into()),
        }
    }
}
fn main() -> Result<(), String> {
    let args: Vec<String> = std::env::args().collect();
    let path = PathBuf::from(
        args.get(2)
            .ok_or("usage: backend_validation validate|smoke CATALOGUE")?,
    );
    let source = fs::read_to_string(path).map_err(|_| "cannot read catalogue")?;
    let catalogue = catalogue::parse(&source, 1)?;
    if args.get(1).map(String::as_str) == Some("validate") {
        println!(
            "{}",
            serde_json::json!({"valid":true,"actions":catalogue.actions.len()})
        );
        return Ok(());
    }
    if args.get(1).map(String::as_str) != Some("smoke") {
        return Err("unknown validation mode".into());
    }
    let temp = tempfile::tempdir().map_err(|_| "fixture directory unavailable")?;
    let path = temp.path().join("catalogue.toml");
    fs::write(&path, &source).map_err(|_| "fixture unavailable")?;
    let engine = Engine::with_runner(path.clone(), temp.path().into(), Arc::new(PublicRunner))?;
    for id in ["summarise_clipboard", "translate_clipboard"] {
        let (result, _) = engine.execute(ExecuteRequest {
            action_id: id.into(),
            args: ExecArgs::default(),
            confirmation_token: None,
            expected_app: false,
        })?;
        println!(
            "{}",
            serde_json::json!({"action":id,"status":result.status,"text":result.text})
        );
    }
    let proposal = engine.propose(vec![
        "Look up public library opening hours online".into(),
        "Find opening hours for a public library".into(),
        "Search public library hours on the web".into(),
    ])?;
    assert_eq!(fs::read_to_string(path).unwrap(), source);
    println!(
        "{}",
        serde_json::json!({"proposalValidated":true,"catalogueUnchanged":true,"toml":proposal.toml})
    );
    Ok(())
}
