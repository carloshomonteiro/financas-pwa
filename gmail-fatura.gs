// Código executado pelo carregador no Apps Script (via eval).
var ENDPOINT = 'https://liyvselfbeuosdcifybp.supabase.co/functions/v1/fatura-email?token=9y2cPfeCkbevZlqr5k9hW5zu-2IWv1J_';

function _post(payload) {
  UrlFetchApp.fetch(ENDPOINT, { method: 'post', contentType: 'application/json', payload: JSON.stringify(payload), muteHttpExceptions: true });
}

// 1) FATURAS DOS BANCOS
var queries = [
  'from:(btgpactual OR xpi.com.br OR c6bank) has:attachment newer_than:10d',
  'subject:fatura has:attachment newer_than:10d'
];
var vistos = {};
var enviados = 0;
var encontrados = [];
for (var q = 0; q < queries.length; q++) {
  var threads = GmailApp.search(queries[q], 0, 20);
  for (var i = 0; i < threads.length; i++) {
    var msgs = threads[i].getMessages();
    for (var j = 0; j < msgs.length; j++) {
      var id = msgs[j].getId();
      if (vistos[id]) continue;
      vistos[id] = true;
      var atts = msgs[j].getAttachments();
      encontrados.push(msgs[j].getFrom() + ' | ' + msgs[j].getSubject() + ' | anexos: ' + atts.length);
      for (var k = 0; k < atts.length; k++) {
        var nome = atts[k].getName().toLowerCase();
        if (nome.slice(-5) !== '.xlsx' && nome.slice(-4) !== '.xls' && nome.slice(-4) !== '.pdf') continue;
        _post({ messageId: id, subject: msgs[j].getSubject(), filename: atts[k].getName(), mimeType: atts[k].getContentType(), data: Utilities.base64Encode(atts[k].getBytes()) });
        enviados++;
      }
    }
  }
}

// 2) ACERTO DE CONTAS DA DÉBORA (corpo do e-mail + anexos) — só armazena candidatos; a escolha é manual no app
var threadsD = GmailApp.search('from:(deboradiasmoreira@gmail.com) newer_than:20d', 0, 10);
var acertos = 0;
for (var i2 = 0; i2 < threadsD.length; i2++) {
  var msgsD = threadsD[i2].getMessages();
  for (var j2 = 0; j2 < msgsD.length; j2++) {
    var m = msgsD[j2];
    if (m.getFrom().indexOf('deboradiasmoreira') === -1) continue; // só mensagens DELA na conversa
    var mid = m.getId();
    var corpo = (m.getPlainBody() || '').trim();
    if (corpo.replace(/\s+/g, ' ').length > 60) {
      _post({ tipo: 'acerto', messageId: mid, subject: m.getSubject(), emailDate: m.getDate().toISOString(), origem: 'corpo do e-mail', texto: corpo.slice(0, 40000) });
      acertos++;
    }
    var attsD = m.getAttachments();
    for (var k2 = 0; k2 < attsD.length; k2++) {
      var nomeD = attsD[k2].getName().toLowerCase();
      if (nomeD.slice(-5) !== '.xlsx' && nomeD.slice(-4) !== '.xls' && nomeD.slice(-4) !== '.pdf') continue;
      _post({ tipo: 'acerto', messageId: mid, subject: m.getSubject(), emailDate: m.getDate().toISOString(), origem: attsD[k2].getName(), filename: attsD[k2].getName(), mimeType: attsD[k2].getContentType(), data: Utilities.base64Encode(attsD[k2].getBytes()) });
      acertos++;
    }
  }
}

// relatório da execução
_post({ messageId: 'resumo-' + Date.now(), filename: '__resumo__', resumo: { emailsEncontrados: encontrados, anexosEnviados: enviados, acertosCapturados: acertos } });
