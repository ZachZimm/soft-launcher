pub fn calc(expr: &str) -> Result<String, String> {
    if expr.is_empty()
        || expr.len() > 256
        || expr
            .bytes()
            .any(|c| !(c.is_ascii_digit() || b" .+-*/^()%eE".contains(&c)))
    {
        return Err("use a bounded arithmetic expression".into());
    }
    let result = meval::eval_str(expr).map_err(|_| "invalid arithmetic expression")?;
    if !result.is_finite() {
        return Err("result is not finite".into());
    }
    Ok(result.to_string())
}
// Canonical scale and offset: base = value * scale + offset.
fn unit(name: &str) -> Option<(&'static str, f64, f64)> {
    Some(match name.trim().to_lowercase().as_str() {
        "m" | "meter" | "metre" => ("length", 1., 0.),
        "km" => ("length", 1000., 0.),
        "cm" => ("length", 0.01, 0.),
        "mm" => ("length", 0.001, 0.),
        "in" | "inch" => ("length", 0.0254, 0.),
        "ft" | "feet" => ("length", 0.3048, 0.),
        "mi" | "mile" => ("length", 1609.344, 0.),
        "kg" => ("mass", 1., 0.),
        "g" => ("mass", 0.001, 0.),
        "lb" | "lbs" => ("mass", 0.45359237, 0.),
        "oz" => ("mass", 0.028349523125, 0.),
        "s" | "sec" => ("time", 1., 0.),
        "min" => ("time", 60., 0.),
        "h" | "hr" => ("time", 3600., 0.),
        "c" | "celsius" => ("temperature", 1., 273.15),
        "f" | "fahrenheit" => ("temperature", 5. / 9., 255.3722222222222),
        "k" | "kelvin" => ("temperature", 1., 0.),
        "b" => ("data", 1., 0.),
        "kb" => ("data", 1000., 0.),
        "mb" => ("data", 1e6, 0.),
        "gb" => ("data", 1e9, 0.),
        "kib" => ("data", 1024., 0.),
        "mib" => ("data", 1048576., 0.),
        _ => return None,
    })
}
pub fn convert(value: f64, from: &str, to: &str) -> Result<String, String> {
    let (dim, scale, offset) = unit(from).ok_or("unknown source unit")?;
    let (target_dim, target_scale, target_offset) = unit(to).ok_or("unknown destination unit")?;
    if dim != target_dim || !value.is_finite() {
        return Err("incompatible units or invalid value".into());
    }
    let result = (value * scale + offset - target_offset) / target_scale;
    if !result.is_finite() {
        return Err("result is not finite".into());
    }
    Ok(format!("{result:.6} {to}"))
}
