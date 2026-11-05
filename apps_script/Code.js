// gsheet-mcp images: floating (over-grid) images, called through the Apps Script API
// (scripts.run) with the caller's own token. The Sheets REST API cannot create, list
// or move these, so this script is the only way in.
//
// OverGridImage has no stable id, so every image this server inserts carries the
// alt-text description `gsheet-mcp:<token>`, and that tag is its id. An image the
// server did not insert gets a derived id, `pos:<sheetId>:<index>`: its tab's numeric
// id and its position in that tab's getImages(). A derived id is only good until the
// images on that tab change; list again after an insert or a delete.
//
// Contract with src/gsheet_mcp/images.py. The four functions keep the parameter order
// of the first deployed version; later parameters are appended, never reordered.

var TAG_PREFIX = 'gsheet-mcp:';
var POS_PREFIX = 'pos:';

function sheetByName_(ss, sheetName) {
  var sheet = sheetName ? ss.getSheetByName(sheetName) : ss.getSheets()[0];
  if (!sheet) throw new Error('No tab named ' + sheetName);
  return sheet;
}

function imageId_(sheet, image, index) {
  var description = image.getAltTextDescription() || '';
  if (description.indexOf(TAG_PREFIX) === 0) return description;
  return POS_PREFIX + sheet.getSheetId() + ':' + index;
}

function describe_(sheet, image, index) {
  var anchor = image.getAnchorCell();
  return {
    id: imageId_(sheet, image, index),
    index: index,
    sheet: sheet.getName(),
    sheetId: sheet.getSheetId(),
    cell: anchor.getA1Notation(),
    offsetX: image.getAnchorCellXOffset(),
    offsetY: image.getAnchorCellYOffset(),
    widthPx: image.getWidth(),
    heightPx: image.getHeight(),
    altTitle: image.getAltTextTitle(),
    altDescription: image.getAltTextDescription()
  };
}

// The one image an id names: { sheet, image, index }. `sheetName`, when given, must
// be the image's tab.
function findImage_(ss, sheetName, id) {
  if (!id) throw new Error('No image id given');
  if (id.indexOf(POS_PREFIX) === 0) {
    var parts = id.slice(POS_PREFIX.length).split(':');
    var sheetId = Number(parts[0]);
    var index = Number(parts[1]);
    if (parts.length !== 2 || !isFinite(sheetId) || !isFinite(index)) {
      throw new Error('Not an image id: ' + id);
    }
    var sheet = null;
    ss.getSheets().forEach(function (s) { if (s.getSheetId() === sheetId) sheet = s; });
    if (!sheet) throw new Error('No tab with id ' + sheetId + ' for image ' + id);
    if (sheetName && sheet.getName() !== sheetName) {
      throw new Error('Image ' + id + ' is on tab ' + sheet.getName() + ', not ' + sheetName);
    }
    var images = sheet.getImages();
    if (index < 0 || index >= images.length) {
      throw new Error('No image ' + id + ': tab ' + sheet.getName() + ' has ' +
                      images.length + ' images now; list them again');
    }
    return { sheet: sheet, image: images[index], index: index };
  }
  var sheets = sheetName ? [sheetByName_(ss, sheetName)] : ss.getSheets();
  var found = [];
  sheets.forEach(function (s) {
    s.getImages().forEach(function (image, i) {
      if (image.getAltTextDescription() === id) found.push({ sheet: s, image: image, index: i });
    });
  });
  if (!found.length) {
    throw new Error('No image ' + id + (sheetName ? ' on tab ' + sheetName : ''));
  }
  if (found.length > 1) {
    var tabs = found.map(function (f) { return f.sheet.getName(); });
    throw new Error('Image id ' + id + ' matches ' + found.length + ' images (tabs ' +
                    tabs.join(', ') + '); name the tab');
  }
  return found[0];
}

// With only one side given, the other keeps the image's aspect ratio.
function resize_(image, widthPx, heightPx) {
  if (widthPx && heightPx) {
    image.setWidth(widthPx);
    image.setHeight(heightPx);
  } else if (widthPx) {
    var h = Math.max(1, Math.round(image.getHeight() * widthPx / image.getWidth()));
    image.setWidth(widthPx);
    image.setHeight(h);
  } else if (heightPx) {
    var w = Math.max(1, Math.round(image.getWidth() * heightPx / image.getHeight()));
    image.setHeight(heightPx);
    image.setWidth(w);
  }
}

function insertImage(spreadsheetId, sheetName, a1, base64, mimeType, widthPx, heightPx, tag,
                     offsetX, offsetY, altTitle) {
  if (!tag || tag.indexOf(TAG_PREFIX) !== 0) throw new Error('A tag starts with ' + TAG_PREFIX);
  var ss = SpreadsheetApp.openById(spreadsheetId);
  var sheet = sheetByName_(ss, sheetName);
  var cell = sheet.getRange(a1);
  var bytes = Utilities.base64Decode(base64);
  var blob = Utilities.newBlob(bytes, mimeType, tag);
  var image = sheet.insertImage(blob, cell.getColumn(), cell.getRow(),
                                offsetX || 0, offsetY || 0);
  resize_(image, widthPx, heightPx);
  image.setAltTextDescription(tag);
  if (altTitle) image.setAltTextTitle(altTitle);
  var found = findImage_(ss, sheet.getName(), tag);
  var result = describe_(found.sheet, found.image, found.index);
  result.bytes = bytes.length;
  return result;
}

function listImages(spreadsheetId, sheetName) {
  var ss = SpreadsheetApp.openById(spreadsheetId);
  var sheets = sheetName ? [sheetByName_(ss, sheetName)] : ss.getSheets();
  var out = [];
  sheets.forEach(function (sheet) {
    sheet.getImages().forEach(function (image, i) { out.push(describe_(sheet, image, i)); });
  });
  return out;
}

function moveImage(spreadsheetId, sheetName, id, a1, widthPx, heightPx, offsetX, offsetY) {
  var ss = SpreadsheetApp.openById(spreadsheetId);
  var found = findImage_(ss, sheetName, id);
  var image = found.image;
  if (a1) image.setAnchorCell(found.sheet.getRange(a1));
  if (offsetX != null) image.setAnchorCellXOffset(offsetX);
  if (offsetY != null) image.setAnchorCellYOffset(offsetY);
  resize_(image, widthPx, heightPx);
  return describe_(found.sheet, image, found.index);
}

function deleteImage(spreadsheetId, sheetName, id) {
  var ss = SpreadsheetApp.openById(spreadsheetId);
  var found = findImage_(ss, sheetName, id);
  var before = describe_(found.sheet, found.image, found.index);
  found.image.remove();
  return before;
}
