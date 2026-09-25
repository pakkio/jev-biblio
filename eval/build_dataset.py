"""Genera eval/dataset.jsonl: articoli brevi con fonti reali e un'etichetta attesa (stelle).

Le etichette sono assegnate da chi ha scritto gli articoli: ogni argomento ha una versione
corretta (4 stelle), una fuorviante (2 stelle: fatti veri con conclusioni distorte) e una
falsa (1 stella). Vanno integrate con articoli reali etichettati da persone.
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
SAMPLES = HERE.parent / "samples"

TOPICS = {
    "apollo11": {
        "bib": "[1] Wikipedia - Apollo 11. https://it.wikipedia.org/wiki/Apollo_11\n"
               "[2] Wikipedia (en) - Apollo 11. https://en.wikipedia.org/wiki/Apollo_11",
        "clean": "La missione Apollo 11 partì il 16 luglio 1969 dal Kennedy Space Center a bordo di un razzo Saturn V [1]. "
                 "L'equipaggio era formato da Neil Armstrong, Buzz Aldrin e Michael Collins [1][2]. "
                 "Il modulo lunare Eagle atterrò il 20 luglio 1969 nel Mare della Tranquillità [1]. "
                 "Armstrong fu il primo uomo a camminare sulla Luna, seguito da Aldrin, mentre Collins rimase in orbita "
                 "nel modulo di comando Columbia [2]. L'equipaggio rientrò sulla Terra il 24 luglio 1969 con un ammaraggio "
                 "nell'Oceano Pacifico [2].",
        "misleading": "Apollo 11: la NASA aveva paura della Luna.\n\n"
                      "La missione Apollo 11 partì il 16 luglio 1969 e portò Armstrong e Aldrin sulla superficie lunare [1]. "
                      "Al rientro gli astronauti furono messi in quarantena per circa tre settimane [2]: la prova che la NASA "
                      "sapeva che sulla Luna esistevano microrganismi pericolosi e lo ha tenuto nascosto. "
                      "Collins rimase in orbita nel modulo di comando [2], evidentemente perché non era ritenuto abbastanza "
                      "affidabile per scendere.",
        "false": "La missione Apollo 11 partì nel 1972 da Cape Canaveral con un equipaggio di quattro astronauti [1]. "
                 "Il primo uomo a mettere piede sulla Luna fu Buzz Aldrin, seguito da Michael Collins [1][2]. "
                 "Gli astronauti rimasero sulla superficie lunare per due settimane e piantarono la prima base permanente [2].",
    },
    "curie": {
        "bib": "[1] Wikipedia - Marie Curie. https://it.wikipedia.org/wiki/Marie_Curie\n"
               "[2] Wikipedia (en) - Marie Curie. https://en.wikipedia.org/wiki/Marie_Curie",
        "clean": "Marie Curie nacque a Varsavia nel 1867 [1]. Nel 1903 vinse il premio Nobel per la fisica insieme "
                 "a Pierre Curie e Henri Becquerel per le ricerche sulla radioattività [1][2]. "
                 "Nel 1911 ottenne il premio Nobel per la chimica per la scoperta del polonio e del radio [2]. "
                 "Fu la prima donna a vincere un premio Nobel ed è l'unica persona ad averlo vinto in due discipline "
                 "scientifiche diverse [2]. Morì nel 1934 di anemia aplastica, probabilmente causata dalla lunga "
                 "esposizione alle radiazioni [1].",
        "misleading": "Marie Curie e il lato oscuro della scienza.\n\n"
                      "Marie Curie vinse il Nobel per la fisica nel 1903 e quello per la chimica nel 1911 [2]. "
                      "Morì nel 1934 di anemia aplastica, probabilmente causata dall'esposizione alle radiazioni [1]. "
                      "La sua morte dimostra che la ricerca sulla radioattività è intrinsecamente mortale per chiunque la "
                      "pratichi, e che andrebbe abbandonata in ogni campo, medicina compresa.",
        "false": "Marie Curie nacque a Parigi nel 1880 [1]. Vinse tre premi Nobel, tutti per la fisica [2]. "
                 "La sua scoperta più importante fu l'uranio, che isolò per la prima volta nel 1920 [1][2].",
    },
    "pisa": {
        "bib": "[1] Wikipedia - Torre di Pisa. https://it.wikipedia.org/wiki/Torre_di_Pisa\n"
               "[2] Wikipedia (en) - Leaning Tower of Pisa. https://en.wikipedia.org/wiki/Leaning_Tower_of_Pisa",
        "clean": "La Torre di Pisa è il campanile della cattedrale di Santa Maria Assunta, in Piazza dei Miracoli [1]. "
                 "La costruzione iniziò nel 1173 e durò circa due secoli, con lunghe interruzioni [1][2]. "
                 "La torre cominciò a inclinarsi già durante la costruzione, a causa del terreno cedevole su cui poggia [2]. "
                 "Tra il 1990 e il 2001 fu chiusa al pubblico per lavori di consolidamento che ne ridussero l'inclinazione [1][2].",
        "misleading": "Torre di Pisa, problema risolto per sempre.\n\n"
                      "La costruzione della Torre di Pisa iniziò nel 1173 [1]. "
                      "Tra il 1990 e il 2001 i lavori di consolidamento ne hanno ridotto l'inclinazione [2]: "
                      "la torre è ormai perfettamente stabile e gli ingegneri garantiscono che non si muoverà mai più, "
                      "per sempre.",
        "false": "La Torre di Pisa fu progettata da Leonardo da Vinci e costruita in soli cinque anni, dal 1350 al 1355 [1]. "
                 "L'inclinazione fu voluta dagli architetti per stupire i visitatori [2]. "
                 "Nel 2001 la torre è stata raddrizzata completamente [1].",
    },
    "muraglia": {
        "bib": "[1] Wikipedia - Grande muraglia cinese. https://it.wikipedia.org/wiki/Grande_muraglia_cinese\n"
               "[2] Wikipedia (en) - Great Wall of China. https://en.wikipedia.org/wiki/Great_Wall_of_China",
        "clean": "La Grande muraglia cinese è un insieme di fortificazioni costruite nell'arco di molti secoli [1]. "
                 "La maggior parte dei tratti oggi visibili risale alla dinastia Ming [1][2]. "
                 "Nel 1987 è stata dichiarata patrimonio dell'umanità dall'UNESCO [1]. "
                 "Contrariamente a una leggenda diffusa, non è visibile a occhio nudo dalla Luna [2].",
        "misleading": "La Muraglia si vede dallo spazio: ecco le prove.\n\n"
                      "La Grande muraglia cinese è patrimonio dell'UNESCO dal 1987 [1]. "
                      "Alcuni astronauti hanno riferito di averla intravista dall'orbita bassa in condizioni particolari [2]: "
                      "è quindi confermato che la Grande muraglia è la costruzione umana più visibile dallo spazio, "
                      "riconoscibile anche dalla Luna.",
        "false": "La Grande muraglia cinese fu costruita interamente in un solo secolo sotto un unico imperatore [1]. "
                 "È l'unica costruzione umana visibile a occhio nudo dalla Luna [2]. "
                 "È lunga esattamente 3.000 chilometri [1][2].",
    },
    "fotosintesi": {
        "bib": "[1] Wikipedia - Fotosintesi clorofilliana. https://it.wikipedia.org/wiki/Fotosintesi_clorofilliana\n"
               "[2] Wikipedia (en) - Photosynthesis. https://en.wikipedia.org/wiki/Photosynthesis",
        "clean": "La fotosintesi clorofilliana è il processo con cui piante, alghe e alcuni batteri convertono l'energia "
                 "luminosa in energia chimica [1][2]. Utilizza anidride carbonica e acqua e produce zuccheri e ossigeno [1]. "
                 "Nelle piante avviene nei cloroplasti, che contengono la clorofilla [1][2].",
        "misleading": "Le foreste non servono a respirare.\n\n"
                      "La fotosintesi produce ossigeno a partire da anidride carbonica e acqua [1]. "
                      "Una parte importante dell'ossigeno atmosferico è prodotta dal fitoplancton marino [2]: "
                      "questo dimostra che le foreste non hanno alcun ruolo nel ciclo dell'ossigeno e che disboscarle "
                      "non ha conseguenze sull'atmosfera.",
        "false": "Durante la fotosintesi le piante assorbono ossigeno e rilasciano anidride carbonica [1]. "
                 "Il processo avviene nei mitocondri, dove si trova la clorofilla [2]. "
                 "La fotosintesi può avvenire solo di notte, quando le piante sono al riparo dal sole [1][2].",
    },
    "python": {
        "bib": "[1] Wikipedia - Python. https://it.wikipedia.org/wiki/Python\n"
               "[2] Wikipedia (en) - Python (programming language). https://en.wikipedia.org/wiki/Python_(programming_language)",
        "clean": "Python è un linguaggio di programmazione creato da Guido van Rossum [1][2]. "
                 "La prima versione fu pubblicata nel 1991 [1][2]. "
                 "Il nome deriva dal gruppo comico britannico Monty Python [1]. "
                 "Python usa l'indentazione per delimitare i blocchi di codice [2], e la versione 3.0, uscita nel 2008, "
                 "non era pienamente compatibile con le versioni precedenti [2].",
        "misleading": "Python, il linguaggio perfetto.\n\n"
                      "Python fu creato da Guido van Rossum e pubblicato nel 1991 [1]. "
                      "Oggi è tra i linguaggi più popolari al mondo secondo diverse classifiche [2]: "
                      "è quindi il linguaggio più veloce in assoluto e la scelta migliore per qualunque tipo di software, "
                      "dai sistemi operativi ai videogiochi.",
        "false": "Python fu creato da Dennis Ritchie ai Bell Labs nel 1972 [1]. "
                 "Il nome fu scelto in onore del serpente pitone, simbolo di velocità [2]. "
                 "Python non usa l'indentazione e i blocchi sono delimitati da parentesi graffe [1][2].",
    },
}


# Set di test: scritto dopo aver fissato le regole di voto, da usare solo per misurare.
# Metà degli argomenti non usa i marcatori [n] nel testo (li associa il LLM).
TEST_TOPICS = {
    "everest": {
        "bib": "[1] Wikipedia - Everest. https://it.wikipedia.org/wiki/Everest\n"
               "[2] Wikipedia (en) - Mount Everest. https://en.wikipedia.org/wiki/Mount_Everest",
        "clean": "L'Everest è la montagna più alta della Terra, con circa 8.849 metri sul livello del mare [1][2]. "
                 "Si trova nella catena dell'Himalaya, sul confine tra Nepal e Cina [1]. "
                 "La prima salita documentata fu compiuta il 29 maggio 1953 da Edmund Hillary e Tenzing Norgay [2]. "
                 "Il nome inglese ricorda il geografo George Everest, mentre in nepalese la montagna è chiamata Sagarmatha [1][2].",
        "misleading": "Everest, ormai una passeggiata.\n\n"
                      "L'Everest è alto circa 8.849 metri [1]. Ogni anno centinaia di alpinisti ne raggiungono la vetta [2]: "
                      "è quindi dimostrato che salire sull'Everest è diventato facile e privo di rischi, alla portata di chiunque.",
        "false": "L'Everest è alto 9.500 metri e si trova nella cordigliera delle Ande [1]. "
                 "La prima salita fu compiuta nel 1920 da George Mallory, che tornò in patria da eroe [2].",
    },
    "penicillina": {
        "bib": "[1] Wikipedia - Penicillina. https://it.wikipedia.org/wiki/Penicillina\n"
               "[2] Wikipedia (en) - Penicillin. https://en.wikipedia.org/wiki/Penicillin",
        "clean": "La penicillina fu scoperta da Alexander Fleming nel 1928, osservando che una muffa del genere Penicillium "
                 "impediva la crescita dei batteri [1][2]. Negli anni Quaranta Howard Florey ed Ernst Chain ne svilupparono "
                 "la produzione come farmaco [2]. Nel 1945 Fleming, Florey e Chain ricevettero il premio Nobel per la medicina [1][2].",
        "misleading": "Antibiotici: ormai inutili.\n\n"
                      "La penicillina fu scoperta da Alexander Fleming nel 1928 [1]. Oggi molti batteri sono diventati "
                      "resistenti alla penicillina [2]: questo significa che gli antibiotici non funzionano più contro "
                      "nessuna infezione e che non ha più senso usarli.",
        "false": "La penicillina fu scoperta da Louis Pasteur nel 1850 [1]. "
                 "È un farmaco antivirale, efficace soprattutto contro l'influenza [2].",
    },
    "galileo": {
        "bib": "[1] Wikipedia - Satelliti medicei. https://it.wikipedia.org/wiki/Satelliti_medicei\n"
               "[2] Wikipedia (en) - Galilean moons. https://en.wikipedia.org/wiki/Galilean_moons",
        "clean": "Nel gennaio 1610 Galileo Galilei scoprì quattro satelliti di Giove con il suo telescopio [1][2]. "
                 "Li chiamò Medicea Sidera in onore della famiglia Medici [1]. "
                 "Oggi sono noti come satelliti galileiani: Io, Europa, Ganimede e Callisto [2].",
        "misleading": "Galileo chiude il dibattito.\n\n"
                      "Nel 1610 Galileo scoprì quattro satelliti che orbitano intorno a Giove [1][2]. "
                      "Questa scoperta dimostrò definitivamente che la Terra gira intorno al Sole, "
                      "e da quel momento nessuno scienziato ebbe più dubbi.",
        "false": "Galileo Galilei scoprì nel 1650 i due satelliti di Marte [1]. "
                 "Li dedicò alla famiglia Sforza di Milano, che finanziava le sue ricerche [2].",
    },
    "titanic": {
        "bib": "[1] Wikipedia - RMS Titanic. https://it.wikipedia.org/wiki/RMS_Titanic\n"
               "[2] Wikipedia (en) - Titanic. https://en.wikipedia.org/wiki/Titanic",
        "clean": "Il Titanic fu costruito nei cantieri Harland and Wolff di Belfast. "
                 "Durante il viaggio inaugurale da Southampton a New York urtò un iceberg e affondò nella notte "
                 "tra il 14 e il 15 aprile 1912. Nel naufragio morirono più di 1.500 persone, anche perché "
                 "le scialuppe di salvataggio non bastavano per tutti.",
        "misleading": "Il Titanic e l'inganno della tecnologia.\n\n"
                      "Il Titanic era considerato praticamente inaffondabile e affondò durante il suo primo viaggio, "
                      "nel 1912. Questo dimostra che la tecnologia moderna rende le navi sempre più pericolose: "
                      "le navi di oggi sono quindi meno sicure di quelle a vela del passato.",
        "false": "Il Titanic affondò nel 1915 nel mar Mediterraneo, colpito da un sottomarino tedesco. "
                 "Grazie alle scialuppe, tutti i passeggeri e l'equipaggio si salvarono.",
    },
    "colosseo": {
        "bib": "[1] Wikipedia - Colosseo. https://it.wikipedia.org/wiki/Colosseo\n"
               "[2] Wikipedia (en) - Colosseum. https://en.wikipedia.org/wiki/Colosseum",
        "clean": "Il Colosseo, o Anfiteatro Flavio, fu fatto costruire dall'imperatore Vespasiano e inaugurato "
                 "da Tito nell'80 d.C. Poteva ospitare decine di migliaia di spettatori, che vi assistevano "
                 "a combattimenti di gladiatori e cacce ad animali. Nel 2007 è stato inserito tra le nuove "
                 "sette meraviglie del mondo.",
        "misleading": "Roma, una città che viveva al Colosseo.\n\n"
                      "Il Colosseo poteva ospitare decine di migliaia di spettatori e vi si svolgevano combattimenti "
                      "di gladiatori. Dunque i Romani passavano la maggior parte delle loro giornate a guardare "
                      "combattimenti, e l'intera economia di Roma si reggeva sugli spettacoli.",
        "false": "Il Colosseo fu costruito dai Greci nel V secolo a.C. "
                 "Era usato soprattutto per le corse delle bighe e poteva contenere al massimo mille persone.",
    },
    "dna": {
        "bib": "[1] Wikipedia - Acido desossiribonucleico. https://it.wikipedia.org/wiki/Acido_desossiribonucleico\n"
               "[2] Wikipedia (en) - DNA. https://en.wikipedia.org/wiki/DNA",
        "clean": "Il DNA contiene le informazioni genetiche degli organismi viventi. "
                 "Nel 1953 James Watson e Francis Crick ne descrissero la struttura a doppia elica, basandosi anche "
                 "sui dati di diffrazione di Rosalind Franklin. Le sue quattro basi azotate sono adenina, timina, "
                 "citosina e guanina, e si appaiano adenina con timina e citosina con guanina.",
        "misleading": "Siamo prigionieri dei nostri geni.\n\n"
                      "Nel 1953 Watson e Crick descrissero la struttura a doppia elica del DNA, che contiene le "
                      "istruzioni genetiche degli organismi. Questo significa che il carattere e il comportamento "
                      "di ogni persona sono interamente decisi dai geni e non possono essere cambiati in alcun modo.",
        "false": "La struttura del DNA fu scoperta da Charles Darwin nel 1859. "
                 "Il DNA è formato da tre sole basi azotate: adenina, guanina e uracile.",
    },
}

EXPECTED = {"clean": 4, "misleading": 2, "false": 1}


def main():
    items = []
    for topic, data in TOPICS.items():
        for variant, stars in EXPECTED.items():
            items.append({"id": f"{topic}-{variant}", "article": data[variant], "bibliography": data["bib"],
                          "expected_stars": stars, "label_by": "costruito"})
    jwst = [("jwst-clean", "jwst_article_clean.txt", "jwst_bibliography_clean.txt", 4),
            ("jwst-misleading", "jwst_article_misleading.txt", "jwst_bibliography_clean.txt", 2),
            ("jwst-false", "jwst_article.txt", "jwst_bibliography.txt", 1)]
    for item_id, article, bibliography, stars in jwst:
        items.append({"id": item_id, "article": (SAMPLES / article).read_text(),
                      "bibliography": (SAMPLES / bibliography).read_text(),
                      "expected_stars": stars, "label_by": "costruito"})
    _write(HERE / "dataset.jsonl", items)
    _write(HERE / "test_dataset.jsonl", [
        {"id": f"{topic}-{variant}", "article": data[variant], "bibliography": data["bib"],
         "expected_stars": stars, "label_by": "costruito"}
        for topic, data in TEST_TOPICS.items() for variant, stars in EXPECTED.items()
    ])


def _write(path, items):
    with open(path, "w") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    print(f"{len(items)} articoli scritti in {path}")


if __name__ == "__main__":
    main()
