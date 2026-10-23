// A formula character (= + - @ or its full-width form) counts after leading spaces, control characters, invisible
// marks and bidi controls too. Keep this class equal to CSV_FORMULA in server/research/terrier_contacts.py.
const FORMULA=/^(?:[\t\r\n]|[\s\u0000-\u001f\u007f-\u009f\u00ad\u061c\u180e\u{200b}-\u{200f}\u{202a}-\u{202e}\u{2060}\u{2066}-\u{2069}]*[=+\-@\u{ff0b}\u{ff0d}\u{ff1d}\u{ff20}])/u;
/** Keeps a value from turning into a formula when the file opens in a spreadsheet (same rule as csv_value in server/research/terrier_contacts.py). */
export function csvValue(value:unknown):string{
  const text=value==null?'':String(value);
  return FORMULA.test(text)?`'${text}`:text;
}

/** Undoes csvValue on a cell a guarded export wrote: only an apostrophe in front of a formula start goes, so a plain 'abc keeps it. */
export function unguard(value:string):string{
  return value.startsWith("'")&&FORMULA.test(value.slice(1))?value.slice(1):value;
}

/** Quoted CSV with CRLF between rows; every cell goes through csvValue. */
export function csvText(rows:readonly (readonly unknown[])[]):string{
  return rows.map(row=>row.map(value=>`"${csvValue(value).replaceAll('"','""')}"`).join(',')).join('\r\n');
}
