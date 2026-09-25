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
    with open(HERE / "dataset.jsonl", "w") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    print(f"{len(items)} articoli scritti in {HERE / 'dataset.jsonl'}")


if __name__ == "__main__":
    main()
