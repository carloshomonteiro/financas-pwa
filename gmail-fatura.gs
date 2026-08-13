// Código executado pelo carregador no Apps Script (via eval).
// Mantido no repositório para poder ser atualizado sem mexer no script.google.com.
var ENDPOINT = 'https://liyvselfbeuosdcifybp.supabase.co/functions/v1/fatura-email?token=9y2cPfeCkbevZlqr5k9hW5zu-2IWv1J_';
var threads = GmailApp.search('from:(btgpactual OR xpi.com.br OR c6bank) has:attachment newer_than:10d', 0, 20);
for (var i = 0; i < threads.length; i++) {
  var msgs = threads[i].getMessages();
  for (var j = 0; j < msgs.length; j++) {
    var atts = msgs[j].getAttachments();
    for (var k = 0; k < atts.length; k++) {
      var nome = atts[k].getName().toLowerCase();
      if (nome.slice(-5) !== '.xlsx' && nome.slice(-4) !== '.xls' && nome.slice(-4) !== '.pdf') continue;
      UrlFetchApp.fetch(ENDPOINT, {
        method: 'post',
        contentType: 'application/json',
        payload: JSON.stringify({
          messageId: msgs[j].getId(),
          subject: msgs[j].getSubject(),
          filename: atts[k].getName(),
          mimeType: atts[k].getContentType(),
          data: Utilities.base64Encode(atts[k].getBytes()),
        }),
        muteHttpExceptions: true,
      });
    }
  }
}
