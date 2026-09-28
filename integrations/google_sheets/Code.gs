/**
 * Bot de dispatch → Google Sheets.
 *
 * À coller dans la feuille : Extensions → Apps Script. Puis Déployer →
 * Nouveau déploiement → Application Web, « Exécuter en tant que : moi »,
 * « Qui a accès : Tout le monde ». L'adresse obtenue va dans la variable
 * GOOGLE_SHEETS_WEBHOOK_URL du bot, et SECRET dans GOOGLE_SHEETS_SECRET.
 *
 * Le script ne fait qu'ajouter des lignes : il refuse toute requête sans le
 * bon SECRET, et n'ajoute jamais deux fois la même course.
 */
const SECRET = 'A_REMPLACER';
const SHEET_NAME = 'Courses';
const HEADERS = ['Numéro', 'Date', 'Heure', 'Franchisé', 'Livreur', 'Adresse', 'Complément', 'Produits', 'Prix (€)'];

function doPost(e) {
  const lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    const data = JSON.parse(e.postData.contents);
    if (data.secret !== SECRET) return json_({ ok: false, error: 'secret' });
    const sheet = sheet_();
    let added = 0;
    (data.rows || []).forEach(function (r) {
      if (exists_(sheet, r.numero)) return;
      sheet.appendRow([r.numero, r.date, r.heure, r.franchise, r.livreur, r.adresse, r.complement, r.produits, r.prix]);
      added++;
    });
    return json_({ ok: true, added: added });
  } catch (err) {
    return json_({ ok: false, error: String(err) });
  } finally {
    lock.releaseLock();
  }
}

function sheet_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sheet = ss.getSheetByName(SHEET_NAME) || ss.insertSheet(SHEET_NAME);
  if (sheet.getLastRow() === 0) {
    sheet.appendRow(HEADERS);
    sheet.setFrozenRows(1);
    sheet.getRange(1, 1, 1, HEADERS.length).setFontWeight('bold');
  }
  return sheet;
}

function exists_(sheet, numero) {
  if (sheet.getLastRow() < 2) return false;
  return sheet.getRange(2, 1, sheet.getLastRow() - 1, 1)
    .createTextFinder(String(numero)).matchEntireCell(true).findNext() !== null;
}

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
