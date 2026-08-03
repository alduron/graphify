// A prop bound to a plain identifier that already resolves to an in-file
// symbol: a module-level const, referenced directly (no hook involved).
const greeting = { text: "hi" };

export function Banner() {
    return <Header label={greeting} />;
}
