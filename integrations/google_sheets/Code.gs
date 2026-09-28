/**
 * Bot de dispatch → feuille « Dispatch » (onglets Lundi … Dimanche).
 *
 * Installation (possible depuis un téléphone, dans Chrome) :
 * 1. script.google.com → Nouveau projet. Colle ce fichier à la place du contenu.
 * 2. Remplis SPREADSHEET_ID (la partie entre /d/ et /edit dans le lien de la feuille)
 *    et SECRET (le même que GOOGLE_SHEETS_SECRET dans Railway).
 *    Si le script est créé depuis la feuille (Extensions → Apps Script),
 *    SPREADSHEET_ID peut rester vide.
 * 3. Déployer → Nouveau déploiement → Application Web, « Exécuter en tant que : moi »,
 *    « Qui a accès : Tout le monde ». L'adresse obtenue (finit par /exec) va dans
 *    GOOGLE_SHEETS_WEBHOOK_URL.
 *
 * Chaque course livrée est écrite dans l'onglet de la nuit, sur la première ligne
 * de commande vide (lignes 2 à 41), colonnes A à N : Vendeur, Livreur, Statut « OK »,
 * Adresse, Paiement (laissé vide), puis 3 produits (Produit, Qté, Prix). Au-delà de
 * 3 produits, la suite va sur la ligne vide suivante. Le numéro de course est gardé
 * dans une note sur la cellule Vendeur : une course n'est jamais écrite deux fois.
 *
 * Noms : le bot connaît « Franchisé 1 », « Livreur 2 »… Dans PARAMETRES, colonnes
 * Q et R à partir de la ligne 4, écris en Q le nom du bot (ou le vrai nom) et en R
 * le nom de la feuille (PNO, Livreur A…). Sans correspondance, le nom du bot est
 * écrit tel quel : choisis ensuite le bon nom dans le menu déroulant.
 *
 * Le script refuse toute requête sans le bon SECRET.
 */
const SPREADSHEET_ID = '';
const SECRET = 'A_REMPLACER';

const FIRST_ROW = 2;
const LAST_ROW = 41;
const COLS = 14; // A à N
const PRODUCTS_PER_ROW = 3;
const NOTE_PREFIX = 'Bot #';
const NAMES_RANGE = 'PARAMETRES!Q4:R40';

function doPost(e) {
  const lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    const data = JSON.parse(e.postData.contents);
    if (data.secret !== SECRET) return json_({ ok: false, error: 'secret' });
    const ss = spreadsheet_();
    const names = names_(ss);
    let added = 0;
    const errors = [];
    (data.rows || []).forEach(function (r) {
      try {
        if (write_(ss, names, r)) added++;
      } catch (err) {
        errors.push('course ' + r.numero + ' : ' + err.message);
      }
    });
    if (errors.length) return json_({ ok: false, added: added, error: errors.join(' ; ') });
    return json_({ ok: true, added: added });
  } catch (err) {
    return json_({ ok: false, error: String(err) });
  } finally {
    lock.releaseLock();
  }
}

function spreadsheet_() {
  return SPREADSHEET_ID ? SpreadsheetApp.openById(SPREADSHEET_ID) : SpreadsheetApp.getActiveSpreadsheet();
}

// Renvoie true si la course a été écrite, false si elle y était déjà.
function write_(ss, names, r) {
  const sheet = ss.getSheetByName(r.onglet);
  if (!sheet) throw new Error('onglet « ' + r.onglet + ' » introuvable');
  const count = LAST_ROW - FIRST_ROW + 1;
  const notes = sheet.getRange(FIRST_ROW, 1, count, 1).getNotes();
  const tag = NOTE_PREFIX + r.numero;
  for (let i = 0; i < count; i++) {
    const note = notes[i][0];
    if (note === tag || note.indexOf(tag + ' ') === 0) return false;
  }

  const lines = (r.lignes && r.lignes.length) ? r.lignes : [{ produit: '', qte: '', prix: '' }];
  const chunks = [];
  for (let i = 0; i < lines.length; i += PRODUCTS_PER_ROW) chunks.push(lines.slice(i, i + PRODUCTS_PER_ROW));

  const values = sheet.getRange(FIRST_ROW, 1, count, COLS).getValues();
  const free = [];
  for (let i = 0; i < count && free.length < chunks.length; i++) {
    if (values[i].every(function (v) { return v === ''; })) free.push(FIRST_ROW + i);
  }
  if (free.length < chunks.length) throw new Error('onglet « ' + r.onglet + ' » plein (lignes ' + FIRST_ROW + ' à ' + LAST_ROW + ')');

  const vendeur = name_(names, r.vendeur, r.vendeur_nom);
  const livreur = name_(names, r.livreur, r.livreur_nom);
  chunks.forEach(function (chunk, k) {
    const out = [vendeur, livreur, r.statut || 'OK', r.adresse || '', ''];
    for (let j = 0; j < PRODUCTS_PER_ROW; j++) {
      const p = chunk[j];
      out.push(p ? p.produit : '', p ? p.qte : '', p && p.prix !== null && p.prix !== undefined ? p.prix : '');
    }
    const rowNum = free[k];
    sheet.getRange(rowNum, 1, 1, COLS).setValues([out]);
    sheet.getRange(rowNum, 1).setNote(chunks.length > 1 ? tag + ' (' + (k + 1) + '/' + chunks.length + ')' : tag);
  });
  return true;
}

// Table de correspondance PARAMETRES!Q:R — nom du bot ou vrai nom → nom de la feuille.
function names_(ss) {
  const map = {};
  let rows = [];
  try {
    rows = ss.getRange(NAMES_RANGE).getValues();
  } catch (err) {
    return map;
  }
  rows.forEach(function (row) {
    const key = key_(row[0]);
    if (key && row[1] !== '') map[key] = String(row[1]).trim();
  });
  return map;
}

function name_(map, botName, realName) {
  return map[key_(botName)] || map[key_(realName)] || botName || realName || '';
}

function key_(v) {
  return String(v || '').trim().toLowerCase();
}

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
