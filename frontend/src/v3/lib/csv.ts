/** Keeps a value from turning into a formula when the file opens in a spreadsheet (same rule as csv_value in server/research/terrier_contacts.py). */
const FORMULA=/^(?:[\t\r\n]|[\s\u0000-\u001f\u007f-\u009f\u{200b}-\u{200f}\u{2060}]*[=+\-@])/u;
export function csvValue(value:unknown):string{
  const text=value==null?'':String(value);
  return FORMULA.test(text)?`'${text}`:text;
}

/** Quoted CSV with CRLF between rows; every cell goes through csvValue. */
export function csvText(rows:readonly (readonly unknown[])[]):string{
  return rows.map(row=>row.map(value=>`"${csvValue(value).replaceAll('"','""')}"`).join(',')).join('\r\n');
}
