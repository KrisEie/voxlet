"""Original, neutral reading prompts for reference capture and reader previews."""
VOICE_PROMPTS = {
    'en': ('English', 'en',
        "Hi, this is my natural speaking voice. On a quiet morning, I opened the window "
        "and watched a few birds in the garden. Later, I called a friend to make plans for "
        "the weekend. Would you like to join us? We can meet at three and walk into town."),
    'nb': ('Norwegian · short sample', 'no',
        "Jeg åpner YouTube i nettleseren og ser på en video om voiceover. "
        "Så tar jeg en screenshot og sjekker mikrofonen og headsettet. "
        "Har du lyst til å høre på opptaket? Jeg vil at stemmen skal høres ut som meg, "
        "akkurat slik jeg snakker til vanlig."),
    'nb_long': ('Norwegian · full reading · 45–90 seconds', 'no',
        "Jeg åpner YouTube i nettleseren, tar en screenshot og prøver å spille inn en voiceover. "
        "Først sjekker jeg mikrofonen og headsettet. Nå vil jeg høre hvordan stemmen min "
        "låter, akkurat slik jeg snakker til vanlig.\n\n"
        "Jeg har lyst til å fortelle litt om dagen min, akkurat slik jeg ville snakket med en venn. "
        "Det regnet da jeg sto opp, så jeg fant fram en varm jakke før jeg gikk ut. På veien "
        "møtte jeg en nabo, og vi ble stående og snakke om helgen.\n\n"
        "Hjemme igjen lagde jeg kaffe og satte meg ved datamaskinen. Først sjekket jeg noen "
        "meldinger, så fortsatte jeg med et prosjekt jeg har jobbet med en stund. Det var "
        "egentlig ganske enkelt, men jeg brukte mer tid enn jeg hadde tenkt. Kjenner du den følelsen?\n\n"
        "Etterpå tok jeg en pause og gikk en liten tur. Da jeg kom tilbake, prøvde jeg "
        "å spille inn et nytt klipp. Det viktigste for meg er at stemmen høres naturlig ut, "
        "med de pausene, ordene og uttrykkene jeg faktisk bruker til vanlig."),
}

READER_EXAMPLES = {
    'en': "This is a new text being read in the voice I selected. I can pause, go back, and continue whenever I like.",
    'no': "Dette er en ny tekst som leses med stemmen jeg har valgt. Jeg kan pause, spole tilbake og fortsette når jeg vil.",
    'mixed': "Jeg åpner YouTube i nettleseren, tar en screenshot og spiller inn en voiceover. Etterpå sjekker jeg mikrofonen og headsettet før jeg fortsetter.",
}
