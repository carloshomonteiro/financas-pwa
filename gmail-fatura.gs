// Código executado pelo carregador no Apps Script (via eval).
var ENDPOINT = 'https://liyvselfbeuosdcifybp.supabase.co/functions/v1/fatura-email?token=9y2cPfeCkbevZlqr5k9hW5zu-2IWv1J_';
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
        UrlFetchApp.fetch(ENDPOINT, {
          method: 'post',
          contentType: 'application/json',
          payload: JSON.stringify({
            messageId: id,
            subject: msgs[j].getSubject(),
            filename: atts[k].getName(),
            mimeType: atts[k].getContentType(),
            data: Utilities.base64Encode(atts[k].getBytes()),
          }),
          muteHttpExceptions: true,
        });
        enviados++;
      }
    }
  }
}
// relatório da execução pro sistema (diagnóstico visível no banco)
UrlFetchApp.fetch(ENDPOINT, {
  method: 'post',
  contentType: 'application/json',
  payload: JSON.stringify({ messageId: 'resumo-' + Date.now(), filename: '__resumo__', resumo: { emailsEncontrados: encontrados, anexosEnviados: enviados } }),
  muteHttpExceptions: true,
});
