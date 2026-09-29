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
 * de commande vide (lignes 2 à 41), colonnes A à N : Vendeur (toujours VENDEUR,
 * « TOTAL »), Livreur, Statut « OK »,
 * Adresse, Paiement (laissé vide), puis 3 produits (Produit, Qté, Prix). Au-delà de
 * 3 produits, la suite va sur la ligne vide suivante. Le numéro de course est gardé
 * dans une note sur la cellule Vendeur : une course n'est jamais écrite deux fois.
 *
 * Vendeur : une ligne dont la seule case remplie est Vendeur = « TOTAL » compte comme
 * vide. Après chaque écriture, les cases Vendeur vides de l'onglet repassent à « TOTAL ».
 * Pour écrire le nom du franchisé à la place, mets VENDEUR = ''.
 *
 * Noms : le bot connaît « Franchisé 1 », « Livreur 2 »… Dans PARAMETRES, colonnes
 * Q et R à partir de la ligne 4, écris en Q le nom du bot (ou le vrai nom) et en R
 * le nom de la feuille (PNO, Livreur A…). Sans correspondance, le nom du bot est
 * écrit tel quel : choisis ensuite le bon nom dans le menu déroulant.
 *
 * Rechargements (« type: recharge ») : écrits dans le tableau Rechargement
 * (RECHARGE_SPREADSHEET_ID), onglet de la nuit, première ligne libre entre 3 et 20
 * (colonnes C à T vides) : A Livreur, B Box, C Cash récupéré, D→R quantités
 * (+ chargé, − repris, colonne trouvée par le nom du produit en ligne 2),
 * S heure, T Ravitailleur. Noms : table PARAMETRES!J4:K40 du tableau Rechargement.
 *
 * Stock (« action: stock ») : stock actuel d'un livreur, calculé EN DIRECT sans attendre les
 * IMPORTRANGE entre fichiers : « chargé net » (SOLDES ②, calculé dans le tableau Rechargement)
 * moins les ventes au statut OK comptées directement dans les 7 onglets de la feuille Dispatch.
 * Si la section ② est introuvable : repli sur SOLDES ① (stock déjà calculé par la feuille).
 *
 * Stock des box (« action: stock_box ») : onglet ORGA du tableau Rechargement, section ④
 * (stock actuel par box) et section ⑤ (total par produit, seuil, statut).
 * Stock de tous les livreurs (« action: stock_livreurs ») : même calcul direct que « stock ».
 *
 * Le script refuse toute requête sans le bon SECRET.
 */
const SPREADSHEET_ID = '';
const SECRET = 'A_REMPLACER';
const RECHARGE_SPREADSHEET_ID = '';

const VENDEUR = 'TOTAL';

const FIRST_ROW = 2;
const LAST_ROW = 41;
const COLS = 14; // A à N
const PRODUCTS_PER_ROW = 3;
const NOTE_PREFIX = 'Bot #';
const NAMES_RANGE = 'PARAMETRES!Q4:R40';

const R_FIRST_ROW = 3;
const R_LAST_ROW = 20;
const R_HEADER_ROW = 2;
const R_COLS = 20;           // A à T
const R_FIRST_PRODUCT_COL = 4; // D
const R_LAST_PRODUCT_COL = 18; // R
const R_NOTE_PREFIX = 'Bot R#';
const R_NAMES_RANGE = 'PARAMETRES!J4:K40';

const STOCK_SHEET = 'SOLDES';
const STOCK_HEADER_ROW = 4;  // ① stock actuel (repli)
const STOCK_FIRST_ROW = 5;
const STOCK_MAX_ROWS = 25;
const STOCK_COLS = 16;       // A (livreur) à P
const LOADED_TITLE = '②';    // titre de la section « chargé net » dans SOLDES
const JOURS = ['Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi', 'Dimanche'];
const SALE_COLS = [[5, 6], [8, 9], [11, 12]];  // (Produit, Qté) 1 à 3, colonnes F-G, I-J, L-M
const ORGA_SHEET = 'ORGA';
const BOX_TITLE = '④';       // stock actuel par box
const TOTAL_TITLE = '⑤';     // total par produit — alerte stock

function doPost(e) {
  const lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    const data = JSON.parse(e.postData.contents);
    if (data.secret !== SECRET) return json_({ ok: false, error: 'secret' });
    if (data.action === 'stock') return json_(stock_(data));
    if (data.action === 'stock_box') return json_(boxStock_());
    if (data.action === 'stock_livreurs') return json_(livreursStock_());
    const rows = data.rows || [];
    const courses = rows.filter(function (r) { return r.type !== 'recharge'; });
    const recharges = rows.filter(function (r) { return r.type === 'recharge'; });
    let added = 0;
    const errors = [];
    if (courses.length) {
      const ss = spreadsheet_();
      const names = names_(ss, NAMES_RANGE);
      courses.forEach(function (r) {
        try {
          if (write_(ss, names, r)) added++;
        } catch (err) {
          errors.push('course ' + r.numero + ' : ' + err.message);
        }
      });
    }
    if (recharges.length) {
      if (!RECHARGE_SPREADSHEET_ID) {
        errors.push('RECHARGE_SPREADSHEET_ID vide dans le script');
      } else {
        const rss = SpreadsheetApp.openById(RECHARGE_SPREADSHEET_ID);
        const rnames = names_(rss, R_NAMES_RANGE);
        recharges.forEach(function (r) {
          try {
            if (writeRecharge_(rss, rnames, r)) added++;
          } catch (err) {
            errors.push('rechargement R' + r.numero + ' : ' + err.message);
          }
        });
      }
    }
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
    if (isFree_(values[i])) free.push(FIRST_ROW + i);
  }
  if (free.length < chunks.length) throw new Error('onglet « ' + r.onglet + ' » plein (lignes ' + FIRST_ROW + ' à ' + LAST_ROW + ')');

  const vendeur = VENDEUR || name_(names, r.vendeur, r.vendeur_nom);
  const livreur = name_(names, r.livreur, r.livreur_nom);
  chunks.forEach(function (chunk, k) {
    const out = [vendeur, livreur, r.statut || 'OK', r.adresse || '', r.paiement || ''];
    for (let j = 0; j < PRODUCTS_PER_ROW; j++) {
      const p = chunk[j];
      out.push(p ? p.produit : '', p ? p.qte : '', p && p.prix !== null && p.prix !== undefined ? p.prix : '');
    }
    const rowNum = free[k];
    sheet.getRange(rowNum, 1, 1, COLS).setValues([out]);
    sheet.getRange(rowNum, 1).setNote(chunks.length > 1 ? tag + ' (' + (k + 1) + '/' + chunks.length + ')' : tag);
  });
  fillVendeur_(sheet);
  return true;
}

// Ligne libre : tout est vide, sauf éventuellement Vendeur = VENDEUR.
function isFree_(row) {
  return row.every(function (v, j) { return v === '' || (j === 0 && VENDEUR && v === VENDEUR); });
}

// Remet VENDEUR dans les cases Vendeur vides des lignes de commande.
function fillVendeur_(sheet) {
  if (!VENDEUR) return;
  const range = sheet.getRange(FIRST_ROW, 1, LAST_ROW - FIRST_ROW + 1, 1);
  const col = range.getValues();
  let changed = false;
  col.forEach(function (c) { if (c[0] === '') { c[0] = VENDEUR; changed = true; } });
  if (changed) range.setValues(col);
}

// Table de correspondance PARAMETRES!Q:R — nom du bot ou vrai nom → nom de la feuille.
// Renvoie true si le rechargement a été écrit, false s'il y était déjà.
function writeRecharge_(ss, names, r) {
  const sheet = ss.getSheetByName(r.onglet);
  if (!sheet) throw new Error('onglet « ' + r.onglet + ' » introuvable');
  const count = R_LAST_ROW - R_FIRST_ROW + 1;
  const tag = R_NOTE_PREFIX + r.numero;
  const notes = sheet.getRange(R_FIRST_ROW, 1, count, 1).getNotes();
  for (let i = 0; i < count; i++) if (notes[i][0] === tag) return false;

  const nProducts = R_LAST_PRODUCT_COL - R_FIRST_PRODUCT_COL + 1;
  const header = sheet.getRange(R_HEADER_ROW, R_FIRST_PRODUCT_COL, 1, nProducts).getDisplayValues()[0];
  const cols = {};
  header.forEach(function (h, j) { if (String(h).trim()) cols[key_(h)] = j; });
  const qty = new Array(nProducts).fill('');
  const unknown = [];
  Object.keys(r.produits || {}).forEach(function (p) {
    const j = cols[key_(p)];
    if (j === undefined) { unknown.push(p); return; }
    qty[j] = (qty[j] === '' ? 0 : qty[j]) + Number(r.produits[p]);
  });
  if (unknown.length) throw new Error('produit absent des colonnes : ' + unknown.join(', '));

  const values = sheet.getRange(R_FIRST_ROW, 1, count, R_COLS).getValues();
  let rowNum = -1;
  for (let i = 0; i < count; i++) {
    // Libre : rien entre C et T (un livreur ou un box seul, sans mouvement, ne compte pas).
    if (values[i].slice(2).every(function (v) { return v === ''; })) { rowNum = R_FIRST_ROW + i; break; }
  }
  if (rowNum < 0) throw new Error('onglet « ' + r.onglet + ' » plein (lignes ' + R_FIRST_ROW + ' à ' + R_LAST_ROW + ')');

  const out = [name_(names, r.livreur, r.livreur_nom), r.box || '', r.cash ? r.cash : '']
    .concat(qty)
    .concat([r.heure ? r.heure + ' (bot)' : '(bot)', name_(names, r.ravitailleur, r.ravitailleur_nom)]);
  sheet.getRange(rowNum, 1, 1, R_COLS).setValues([out]);
  sheet.getRange(rowNum, 1).setNote(tag);
  return true;
}

// Stock actuel d'un livreur : chargé net (SOLDES ②) − ventes OK lues en direct dans Dispatch.
function stock_(data) {
  if (!RECHARGE_SPREADSHEET_ID) return { ok: false, error: 'RECHARGE_SPREADSHEET_ID vide dans le script' };
  const rss = SpreadsheetApp.openById(RECHARGE_SPREADSHEET_ID);
  const name = name_(names_(rss, R_NAMES_RANGE), data.livreur, data.livreur_nom);
  const sheet = rss.getSheetByName(STOCK_SHEET);
  if (!sheet) return { ok: false, error: 'onglet « ' + STOCK_SHEET + ' » introuvable' };

  const loaded = section_(sheet, LOADED_TITLE, name);
  if (!loaded.found) {
    const current = sectionAt_(sheet, STOCK_HEADER_ROW, name);
    return { ok: true, livreur: name, stock: current.found ? current.values : null, source: 'soldes' };
  }
  const ds = spreadsheet_();
  const dname = name_(names_(ds, NAMES_RANGE), data.livreur, data.livreur_nom);
  const all = soldAll_(ds);
  const sold = {};
  [key_(name), key_(dname)].filter(function (k, i, a) { return a.indexOf(k) === i; }).forEach(function (k) {
    Object.keys(all[k] || {}).forEach(function (p) { sold[p] = (sold[p] || 0) + all[k][p]; });
  });
  const stock = {};
  Object.keys(loaded.values).forEach(function (p) { stock[p] = loaded.values[p] - (sold[key_(p)] || 0); });
  return { ok: true, livreur: name, stock: stock, source: 'direct' };
}

// Stock actuel de chaque box (ORGA ④) et total par produit avec seuil et statut (ORGA ⑤).
function boxStock_() {
  if (!RECHARGE_SPREADSHEET_ID) return { ok: false, error: 'RECHARGE_SPREADSHEET_ID vide dans le script' };
  const sheet = SpreadsheetApp.openById(RECHARGE_SPREADSHEET_ID).getSheetByName(ORGA_SHEET);
  if (!sheet) return { ok: false, error: 'onglet « ' + ORGA_SHEET + ' » introuvable' };
  const col = sheet.getRange(1, 1, 200, 1).getDisplayValues();
  const boxes = {};
  const totals = [];
  for (let i = 0; i < col.length; i++) {
    const title = String(col[i][0]).trim();
    if (title.indexOf(BOX_TITLE) === 0) {
      const header = sheet.getRange(i + 2, 2, 1, STOCK_COLS - 1).getDisplayValues()[0];
      const rows = sheet.getRange(i + 3, 1, STOCK_MAX_ROWS, STOCK_COLS).getValues();
      for (let r = 0; r < rows.length && String(rows[r][0]).trim() !== ''; r++) {
        const values = {};
        header.forEach(function (p, j) { if (String(p).trim()) values[String(p).trim()] = Number(rows[r][j + 1]) || 0; });
        boxes[String(rows[r][0]).trim()] = values;
      }
    } else if (title.indexOf(TOTAL_TITLE) === 0) {
      const rows = sheet.getRange(i + 3, 1, 40, 6).getDisplayValues();
      for (let r = 0; r < rows.length && String(rows[r][0]).trim() !== ''; r++) {
        totals.push({ produit: rows[r][0], box: num_(rows[r][1]), livreurs: num_(rows[r][2]),
                      total: num_(rows[r][3]), seuil: num_(rows[r][4]), statut: rows[r][5] });
      }
    }
  }
  return { ok: true, boxes: boxes, totals: totals };
}

// Stock de chaque livreur de SOLDES ② : chargé net − ventes OK lues en direct dans Dispatch.
function livreursStock_() {
  if (!RECHARGE_SPREADSHEET_ID) return { ok: false, error: 'RECHARGE_SPREADSHEET_ID vide dans le script' };
  const sheet = SpreadsheetApp.openById(RECHARGE_SPREADSHEET_ID).getSheetByName(STOCK_SHEET);
  if (!sheet) return { ok: false, error: 'onglet « ' + STOCK_SHEET + ' » introuvable' };
  const col = sheet.getRange(1, 1, 200, 1).getDisplayValues();
  let headerRow = -1;
  for (let i = 0; i < col.length; i++) if (String(col[i][0]).trim().indexOf(LOADED_TITLE) === 0) { headerRow = i + 2; break; }
  if (headerRow < 0) return { ok: false, error: 'section « ' + LOADED_TITLE + ' » introuvable dans ' + STOCK_SHEET };
  const header = sheet.getRange(headerRow, 2, 1, STOCK_COLS - 1).getDisplayValues()[0];
  const rows = sheet.getRange(headerRow + 1, 1, STOCK_MAX_ROWS, STOCK_COLS).getValues();
  const sold = soldAll_(spreadsheet_());
  const out = {};
  for (let r = 0; r < rows.length && String(rows[r][0]).trim() !== ''; r++) {
    const name = String(rows[r][0]).trim();
    const mine = sold[key_(name)] || {};
    const values = {};
    header.forEach(function (p, j) {
      if (String(p).trim()) values[String(p).trim()] = (Number(rows[r][j + 1]) || 0) - (mine[key_(p)] || 0);
    });
    out[name] = values;
  }
  return { ok: true, livreurs: out };
}

function num_(v) {
  const n = Number(String(v).replace(/\s/g, '').replace(',', '.'));
  return isNaN(n) ? 0 : n;
}

// Ligne d'un livreur dans la section de SOLDES dont le titre (colonne A) commence par `title`.
function section_(sheet, title, name) {
  const col = sheet.getRange(1, 1, 200, 1).getDisplayValues();
  for (let i = 0; i < col.length; i++) {
    if (String(col[i][0]).trim().indexOf(title) === 0) return sectionAt_(sheet, i + 2, name);
  }
  return { found: false, values: {} };
}

function sectionAt_(sheet, headerRow, name) {
  const header = sheet.getRange(headerRow, 2, 1, STOCK_COLS - 1).getDisplayValues()[0];
  const rows = sheet.getRange(headerRow + 1, 1, STOCK_MAX_ROWS, STOCK_COLS).getValues();
  for (let i = 0; i < rows.length; i++) {
    if (String(rows[i][0]).trim() === '') break;
    if (key_(rows[i][0]) !== key_(name)) continue;
    const values = {};
    header.forEach(function (p, j) {
      if (String(p).trim()) values[String(p).trim()] = Number(rows[i][j + 1]) || 0;
    });
    return { found: true, values: values };
  }
  return { found: false, values: {} };
}

// Ventes au statut OK de chaque livreur, lues directement dans les 7 onglets de la feuille Dispatch :
// { livreur (minuscules) : { produit (minuscules) : quantité } }.
function soldAll_(ds) {
  const sold = {};
  JOURS.forEach(function (day) {
    const sheet = ds.getSheetByName(day);
    if (!sheet) return;
    const values = sheet.getRange(FIRST_ROW, 1, LAST_ROW - FIRST_ROW + 1, COLS).getValues();
    values.forEach(function (row) {
      if (key_(row[2]) !== 'ok' || !key_(row[1])) return;
      const who = sold[key_(row[1])] = sold[key_(row[1])] || {};
      SALE_COLS.forEach(function (pq) {
        const p = key_(row[pq[0]]);
        const q = Number(row[pq[1]]) || 0;
        if (p && q) who[p] = (who[p] || 0) + q;
      });
    });
  });
  return sold;
}

function names_(ss, a1) {
  const map = {};
  let rows = [];
  try {
    rows = ss.getRange(a1).getValues();
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
