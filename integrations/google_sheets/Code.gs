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
 * Dépenses des livreurs (« type: depense ») : écrites dans l'onglet de la nuit de la feuille
 * Dispatch, zone DÉPENSES LIVREURS (lignes 46 à 57) : A Livreur, B Type (Charges / Paye),
 * C Montant, D Motif. Numéro gardé dans une note sur la cellule Livreur (« Bot D#12 »).
 *
 * Caisse (« action: cash_livreurs ») : cash que chaque livreur doit avoir sur lui, calculé EN
 * DIRECT comme SOLDES ④ du tableau Rechargement : ventes OK payées en Espèces (feuille Dispatch)
 * − dépenses (lignes 46 à 57) − cash récupéré (colonne C des onglets du tableau Rechargement).
 *
 * Clôture de la semaine (« action: cloture », commande /cloture du bot) :
 * 1. copie des 3 fichiers dans le dossier Drive « Archives bot » (si la copie échoue, rien n'est effacé) ;
 * 2. stock actuel des box (ORGA ④) → nouveau stock initial (COMPTA, onglet STOCK) ;
 * 3. efface la semaine : MOUVEMENTS et RAVI (COMPTA), lignes de commande et dépenses des
 *    7 onglets (Dispatch, la colonne Vendeur repasse à TOTAL), lignes des 7 onglets (Rechargement) ;
 * 4. le stock encore chez chaque livreur est reporté : une ligne « Report clôture » (sans box)
 *    dans l'onglet du jour du tableau Rechargement.
 * Il faut COMPTA_SPREADSHEET_ID et l'accès à Google Drive : après avoir collé le script,
 * choisis la fonction « autoriser », ▶ Exécuter et accepte, puis Déployer → Gérer les
 * déploiements → ✏️ → Version : Nouvelle version (l'adresse /exec ne change pas).
 *
 * Le script refuse toute requête sans le bon SECRET.
 */
const SPREADSHEET_ID = '';
const SECRET = 'A_REMPLACER';
const RECHARGE_SPREADSHEET_ID = '';
const COMPTA_SPREADSHEET_ID = '';
const ARCHIVE_FOLDER = 'Archives bot';

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
const DEP_FIRST_ROW = 46;    // dépenses des livreurs : A Livreur, B Type, C Montant, D Motif
const DEP_LAST_ROW = 57;
const DEP_COLS = 4;
const DEP_NOTE_PREFIX = 'Bot D#';
const PRICE_COLS = [7, 10, 13];  // Prix 1 à 3, colonnes H, K, N
const PAY_COL = 4;               // E : Paiement
const C_STOCK_SHEET = 'STOCK';   // COMPTA : en-têtes produits ligne 2 (B à P), box lignes 4 à 6
const C_STOCK_HEADER_ROW = 2;
const C_STOCK_FIRST_ROW = 4;
const C_STOCK_ROWS = 3;
const C_MOVES = 'MOUVEMENTS!A3:P22';
const C_RAVI = 'RAVI!A5:E15';
const REPORT_LABEL = 'Report clôture';

function doPost(e) {
  const lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    const data = JSON.parse(e.postData.contents);
    if (data.secret !== SECRET) return json_({ ok: false, error: 'secret' });
    if (data.action === 'stock') return json_(stock_(data));
    if (data.action === 'stock_box') return json_(boxStock_());
    if (data.action === 'stock_livreurs') return json_(livreursStock_());
    if (data.action === 'cash_livreurs') return json_(cashLivreurs_());
    if (data.action === 'cloture') return json_(cloture_(data));
    const rows = data.rows || [];
    const courses = rows.filter(function (r) { return r.type !== 'recharge' && r.type !== 'depense'; });
    const recharges = rows.filter(function (r) { return r.type === 'recharge'; });
    const depenses = rows.filter(function (r) { return r.type === 'depense'; });
    let added = 0;
    const errors = [];
    if (courses.length || depenses.length) {
      const ss = spreadsheet_();
      const names = names_(ss, NAMES_RANGE);
      courses.forEach(function (r) {
        try {
          if (write_(ss, names, r)) added++;
        } catch (err) {
          errors.push('course ' + r.numero + ' : ' + err.message);
        }
      });
      depenses.forEach(function (r) {
        try {
          if (writeDepense_(ss, names, r)) added++;
        } catch (err) {
          errors.push('dépense D' + r.numero + ' : ' + err.message);
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

// Renvoie true si la dépense a été écrite, false si elle y était déjà.
function writeDepense_(ss, names, r) {
  const sheet = ss.getSheetByName(r.onglet);
  if (!sheet) throw new Error('onglet « ' + r.onglet + ' » introuvable');
  const count = DEP_LAST_ROW - DEP_FIRST_ROW + 1;
  const tag = DEP_NOTE_PREFIX + r.numero;
  const notes = sheet.getRange(DEP_FIRST_ROW, 1, count, 1).getNotes();
  for (let i = 0; i < count; i++) if (notes[i][0] === tag) return false;
  const values = sheet.getRange(DEP_FIRST_ROW, 1, count, DEP_COLS).getValues();
  let rowNum = -1;
  for (let i = 0; i < count; i++) {
    if (values[i].every(function (v) { return v === ''; })) { rowNum = DEP_FIRST_ROW + i; break; }
  }
  if (rowNum < 0) throw new Error('dépenses de « ' + r.onglet + ' » pleines (lignes ' + DEP_FIRST_ROW + ' à ' + DEP_LAST_ROW + ')');
  sheet.getRange(rowNum, 1, 1, DEP_COLS).setValues([[name_(names, r.livreur, r.livreur_nom), r.depense || '',
                                                     Number(r.montant) || 0, r.motif || '']]);
  sheet.getRange(rowNum, 1).setNote(tag);
  return true;
}

// Cash de chaque livreur, en direct : ventes OK en espèces − dépenses − cash récupéré.
function cashLivreurs_() {
  const out = {};
  function who(name) {
    const k = key_(name);
    if (!out[k]) out[k] = { nom: String(name).trim(), especes: 0, virement: 0, depenses: 0, recupere: 0 };
    return out[k];
  }
  const ds = spreadsheet_();
  JOURS.forEach(function (day) {
    const sheet = ds.getSheetByName(day);
    if (!sheet) return;
    sheet.getRange(FIRST_ROW, 1, LAST_ROW - FIRST_ROW + 1, COLS).getValues().forEach(function (row) {
      if (key_(row[2]) !== 'ok' || !key_(row[1])) return;
      const total = PRICE_COLS.reduce(function (s, c) { return s + num_(row[c]); }, 0);
      const pay = key_(row[PAY_COL]);
      if (pay === 'espèces' || pay === 'especes') who(row[1]).especes += total;
      else if (pay === 'virement') who(row[1]).virement += total;
    });
    sheet.getRange(DEP_FIRST_ROW, 1, DEP_LAST_ROW - DEP_FIRST_ROW + 1, 3).getValues().forEach(function (row) {
      if (key_(row[0]) && num_(row[2])) who(row[0]).depenses += num_(row[2]);
    });
  });
  if (RECHARGE_SPREADSHEET_ID) {
    const rss = SpreadsheetApp.openById(RECHARGE_SPREADSHEET_ID);
    JOURS.forEach(function (day) {
      const sheet = rss.getSheetByName(day);
      if (!sheet) return;
      sheet.getRange(R_FIRST_ROW, 1, R_LAST_ROW - R_FIRST_ROW + 1, 3).getValues().forEach(function (row) {
        if (key_(row[0]) && num_(row[2])) who(row[0]).recupere += num_(row[2]);
      });
    });
  }
  const livreurs = {};
  let recupere = 0;
  Object.keys(out).forEach(function (k) {
    const c = out[k];
    c.cash = Math.round((c.especes - c.depenses - c.recupere) * 100) / 100;
    recupere += c.recupere;
    livreurs[c.nom] = c;
  });
  // Reste chez le ravitailleur (comme COMPTA RAVI) : cash récupéré − ce qu'il a noté dans RAVI.
  let ravi = null;
  if (COMPTA_SPREADSHEET_ID) {
    const used = SpreadsheetApp.openById(COMPTA_SPREADSHEET_ID).getRange(C_RAVI).getValues()
      .reduce(function (s, row) { return s + num_(row[2]); }, 0);
    ravi = Math.round((recupere - used) * 100) / 100;
  }
  return { ok: true, livreurs: livreurs, ravitailleur: ravi };
}

// À exécuter une fois depuis l'éditeur (▶) pour autoriser l'accès à Google Drive (archives).
function autoriser() {
  DriveApp.getRootFolder();
  return 'ok';
}

// Clôture de la semaine. data.onglet : onglet du jour où reporter le stock des livreurs ;
// data.libelle : nom des copies d'archive (« Clôture du 2026-09-28 »).
function cloture_(data) {
  if (!RECHARGE_SPREADSHEET_ID) return { ok: false, error: 'RECHARGE_SPREADSHEET_ID vide dans le script' };
  if (!COMPTA_SPREADSHEET_ID) return { ok: false, error: 'COMPTA_SPREADSHEET_ID vide dans le script' };
  const ds = spreadsheet_();
  const rss = SpreadsheetApp.openById(RECHARGE_SPREADSHEET_ID);
  const cs = SpreadsheetApp.openById(COMPTA_SPREADSHEET_ID);
  if (JOURS.indexOf(data.onglet) < 0) return { ok: false, error: 'onglet du jour inconnu : ' + data.onglet };

  // 1. Tout lire AVANT d'effacer.
  const boxes = boxStock_();
  if (!boxes.ok) return boxes;
  const livreurs = livreursStock_();
  if (!livreurs.ok) return livreurs;

  // 2. Archives : si la copie échoue, on s'arrête sans rien effacer.
  const label = String(data.libelle || ('Clôture du ' + Utilities.formatDate(new Date(), 'Europe/Paris', 'yyyy-MM-dd')));
  const found = DriveApp.getFoldersByName(ARCHIVE_FOLDER);
  const root = found.hasNext() ? found.next() : DriveApp.createFolder(ARCHIVE_FOLDER);
  const folder = root.createFolder(label);
  [ds, rss, cs].forEach(function (f) {
    DriveApp.getFileById(f.getId()).makeCopy(f.getName() + ' — ' + label, folder);
  });

  // 3. Nouveau stock initial des box = stock actuel (ORGA ④), rangé selon les en-têtes du COMPTA.
  const stockSheet = cs.getSheetByName(C_STOCK_SHEET);
  if (!stockSheet) return { ok: false, error: 'onglet « ' + C_STOCK_SHEET + ' » introuvable dans le COMPTA' };
  const header = stockSheet.getRange(C_STOCK_HEADER_ROW, 2, 1, STOCK_COLS - 1).getDisplayValues()[0];
  const boxRange = stockSheet.getRange(C_STOCK_FIRST_ROW, 1, C_STOCK_ROWS, STOCK_COLS);
  const boxRows = boxRange.getValues();
  const byBox = {};
  Object.keys(boxes.boxes).forEach(function (b) { byBox[key_(b)] = boxes.boxes[b]; });
  const initial = {};
  boxRows.forEach(function (row) {
    const values = byBox[key_(row[0])];
    if (!values) return;
    const byProduct = {};
    Object.keys(values).forEach(function (p) { byProduct[key_(p)] = values[p]; });
    initial[String(row[0]).trim()] = {};
    header.forEach(function (p, j) {
      if (!String(p).trim()) return;
      const q = byProduct[key_(p)] || 0;
      row[j + 1] = q === 0 ? '' : q;
      if (q) initial[String(row[0]).trim()][String(p).trim()] = q;
    });
  });
  // Colonnes B à P seulement : les noms des box (colonne A) sont des formules.
  stockSheet.getRange(C_STOCK_FIRST_ROW, 2, C_STOCK_ROWS, STOCK_COLS - 1)
    .setValues(boxRows.map(function (row) { return row.slice(1); }));

  // 4. Effacer la semaine.
  cs.getRange(C_MOVES).clearContent();
  cs.getRange(C_RAVI).clearContent();
  JOURS.forEach(function (day) {
    const sheet = ds.getSheetByName(day);
    if (sheet) {
      const n = LAST_ROW - FIRST_ROW + 1;
      sheet.getRange(FIRST_ROW, 2, n, COLS - 1).clearContent();
      const vendeur = sheet.getRange(FIRST_ROW, 1, n, 1);
      vendeur.clearNote();
      vendeur.setValues(Array.from({ length: n }, function () { return [VENDEUR]; }));
      const dep = sheet.getRange(DEP_FIRST_ROW, 1, DEP_LAST_ROW - DEP_FIRST_ROW + 1, DEP_COLS);
      dep.clearContent();
      dep.clearNote();
    }
    const rsheet = rss.getSheetByName(day);
    if (rsheet) {
      const r = rsheet.getRange(R_FIRST_ROW, 1, R_LAST_ROW - R_FIRST_ROW + 1, R_COLS);
      r.clearContent();
      r.clearNote();
    }
  });

  // 5. Report du stock encore chez les livreurs (sans box : les box ne bougent pas).
  const rsheet = rss.getSheetByName(data.onglet);
  const nProducts = R_LAST_PRODUCT_COL - R_FIRST_PRODUCT_COL + 1;
  const rheader = rsheet.getRange(R_HEADER_ROW, R_FIRST_PRODUCT_COL, 1, nProducts).getDisplayValues()[0];
  const reports = {};
  let rowNum = R_FIRST_ROW;
  Object.keys(livreurs.livreurs).forEach(function (name) {
    const values = livreurs.livreurs[name];
    const byProduct = {};
    Object.keys(values).forEach(function (p) { byProduct[key_(p)] = values[p]; });
    const qty = rheader.map(function (p) { return String(p).trim() && byProduct[key_(p)] ? byProduct[key_(p)] : ''; });
    if (qty.every(function (q) { return q === ''; }) || rowNum > R_LAST_ROW) return;
    rsheet.getRange(rowNum, 1, 1, R_COLS).setValues([[name, '', ''].concat(qty).concat([REPORT_LABEL, ''])]);
    rsheet.getRange(rowNum, 1).setNote(REPORT_LABEL);
    reports[name] = {};
    rheader.forEach(function (p, j) { if (qty[j] !== '') reports[name][String(p).trim()] = qty[j]; });
    rowNum++;
  });
  return { ok: true, archive: folder.getUrl(), dossier: ARCHIVE_FOLDER + ' / ' + label,
           initial: initial, reports: reports };
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
