import argparse
import csv
import json
import re

from datetime import datetime, timedelta
from locale import atof, setlocale, LC_NUMERIC

# TODO
# - tu pourras valider que les champs Indicateur (1|2|3|...) (en fin du google
#   sheet) sont bien exactement les mêmes que nb_pt_obtenu_tab(1|2|3|...) dans
#   l'export solen ?
# - gérer import champ de type date

CELL_SKIPPABLE_VALUES = ["", "-", "NC", "non applicable", "non calculable"]
SOLEN_URL_PREFIX = "https://solen1.enquetes.social.gouv.fr/cgi-bin/HE/P?P="
RE_ARRAY_INDEX = re.compile(r"^(\w+)\[(\d)\]$")


def toPath(struct, path, value, toIndex=None):
    part, *remaining = path.split(".")
    arrayIndex = RE_ARRAY_INDEX.match(part)
    if arrayIndex is not None:
        (arrayName, arrayIndex) = arrayIndex.groups()
        if arrayName not in struct:
            struct[arrayName] = []
        return toPath(struct[arrayName], ".".join(remaining), value, toIndex=arrayIndex)
    elif isinstance(struct, list) and toIndex is not None:
        # FIXME: use toIndex to ensure insertion is done at the proper position in the list
        if path == "":
            struct.append(value)
        else:
            struct.append(toPath({}, path, value))
        return struct
    elif len(remaining) == 0:
        struct[part] = value
        return struct
    else:
        if part not in struct:
            struct[part] = {}
        return toPath(struct[part], ".".join(remaining), value)


class RowImporter(object):
    def __init__(self, row):
        self.row = row
        self.record = {}

    def importField(self, csvFieldName, path, type=str):
        value = self.get(csvFieldName)
        if not value:
            return
        elif type != str:
            try:
                value = type(value)
            except Exception as err:
                raise ValueError("Couldn't cast {0} field value ('{1}') to {2}".format(csvFieldName, value, type))

        return self.set(path, value)

    def importBooleanField(self, csvFieldName, path, negate=False):
        # Note: si la valeur du champ n'est ni "Oui" no "Non", nous n'importons pas la donnée
        if self.get(csvFieldName) == "Oui":
            self.set(path, True if not negate else False)
        elif self.get(csvFieldName) == "Non":
            self.set(path, False if not negate else True)

    def importFloatField(self, csvFieldName, path):
        # Note: nous utilisons la notation décimale française, d'où l'utilisation de la fonction atof
        return self.importField(csvFieldName, path, type=atof)

    def importIntField(self, csvFieldName, path):
        return self.importField(csvFieldName, path, type=int)

    def get(self, csvFieldName):
        if csvFieldName not in self.row:
            raise KeyError("Row does not have a {0} field".format(csvFieldName))
        if self.row[csvFieldName] in CELL_SKIPPABLE_VALUES:
            return None
        return self.row[csvFieldName]

    def set(self, path, value):
        if value not in CELL_SKIPPABLE_VALUES:
            toPath(self.record, path, value)
            return value

    def toKintoRecord(self):
        solenId = self.get("URL d'impression du répondant").replace(SOLEN_URL_PREFIX, "")
        return {"id": solenId, "data": self.record}

    def importPeriodeDeReference(self):
        # Année et périmètre retenus pour le calcul et la publication des indicateurs
        annee_indicateur = self.importIntField("annee_indicateurs", "informationsComplementaires.anneeDeclaration")
        self.importField("structure", "informationsComplementaires.structure")

        # Compatibilité egapro de la tranche d'effectifs
        tranche = self.get("tranche_effectif")
        if tranche == "De 50 à 250 inclus":
            tranche = "50 à 250"
        self.set("informations.trancheEffectifs", tranche)

        # Période de référence
        date_debut_pr = self.importField("date_debut_pr > Valeur date", "informations.debutPeriodeReference")
        if self.get("periode_ref") == "ac":
            # année civile: 31 décembre de l'année précédent "annee_indicateurs"
            debutPeriodeReference = "01/01/" + str(annee_indicateur - 1)
            finPeriodeReference = "31/12/" + str(annee_indicateur - 1)
        elif date_debut_pr != "-":
            # autre période: rajouter un an à "date_debut_pr"
            debutPeriodeReference = date_debut_pr
            finPeriodeReference = (datetime.strptime(date_debut_pr, "%d/%m/%Y") + timedelta(days=365)).strftime("%d/%m/%Y")
        else:
            # autre période de référence sans début spécifié: erreur
            raise RuntimeError("Données de période de référence incohérentes.")
        self.set("informations.debutPeriodeReference", debutPeriodeReference)
        self.set("informations.finPeriodeReference", finPeriodeReference)

        # Note: utilisation d'un nombre à virgule pour prendre en compte les temps partiels
        self.importFloatField("nb_salaries > Valeur numérique", "effectifs.nombreSalariesTotal")

    def importInformationsDeclarant(self):
        # Identification du déclarant pour tout contact ultérieur
        self.importField("Nom", "informationsDeclarant.nom")
        self.importField("Prénom", "informationsDeclarant.prenom")
        self.importField("telephone", "informationsDeclarant.tel")
        self.importField("Reg", "informationsDeclarant.region")
        self.importField("dpt", "informationsDeclarant.departement")
        self.importField("Adr ets", "informationsDeclarant.adresse")
        self.importField("CP", "informationsDeclarant.codePosal")
        self.importField("Commune", "informationsDeclarant.commune")

    def importEntreprise(self):
        self.importField("RS_ets", "informationsEntreprise.nomEntreprise")
        self.importField("SIREN_ets", "informationsEntreprise.siren")
        self.importField("Code NAF", "informationsEntreprise.codeNaf")  # attention format

    def importUES(self):
        self.importField("nom_UES", "informationsEntreprise.nomUES")
        self.importField("nom_ets_UES", "informationsEntreprise.nomsEntreprisesUES")
        self.importField("SIREN_UES", "informationsEntreprise.sirenUES")
        self.importField("Code NAF de cette entreprise", "informationsEntreprise.codeNAF")  # attention format
        self.importIntField("Nb_ets_UES", "informationsEntreprise.nombresEntreprises")

    def importNiveauResultat(self):
        # Niveau de résultat de l'entreprise ou de l'UES
        self.importField("date_publ_niv > Valeur date", "declaration.datePublication")
        self.importField("site_internet_publ", "declaration.lienPublication")

    def importIndicateurUn(self):
        # Indicateur 1 relatif à l'écart de rémunération entre les femmes et les hommes
        # Quatre items possibles :
        # - coef_niv: Par niveau ou coefficient hiérarchique en application de la classification de branche
        # - amc: Par niveau ou coefficient hiérarchique en application d'une autre méthode de cotation des postes
        # - csp: Par catégorie socio-professionnelle
        # - nc: L'indicateur n'est pas calculable
        # Mapping:
        # csp       -> indicateurUn.csp
        # coef_nif  -> indicateurUn.coefficients
        # nc et amc -> indicateurUn.autre
        modalite = self.get("modalite_calc_tab1")
        self.set("indicateurUn.csp", False)
        self.set("indicateurUn.coefficients", False)
        self.set("indicateurUn.autre", False)
        self.set("indicateurUn.nonCalculable", False)
        if modalite == "csp":
            self.set("indicateurUn.csp", True)
        elif modalite == "coefficients":
            self.set("indicateurUn.coefficients", True)
        elif modalite == "amc":
            self.set("indicateurUn.autre", True)
        elif modalite == "nc":
            self.set("indicateurUn.autre", True)
            self.set("indicateurUn.nonCalculable", True)
        self.importField("date_consult_CSE > Valeur date", "indicateurUn.dateConsultationCSE")
        self.importIntField("nb_coef_niv", "indicateurUn.nombreCoefficients")
        self.importField("motif_non_calc_tab1", "indicateurUn.motifNonCalculable")
        self.importField("precision_am_tab1", "indicateurUn.motifNonCalculablePrecision")
        # FIXME: pour le moment un bug de formattage de cellule nous empêche de
        # connaître à quelle tranche d'âge correspond chaque chiffre dans la cellule
        # TODO: import colonnes Ou Em, TAM, IC, niv01 à niv50
        # Résultat
        self.importFloatField("resultat_tab1", "indicateurUn.resultatFinal")
        self.importField("population_favorable_tab1", "indicateurUn.sexeSurRepresente")
        self.importIntField("nb_pt_obtenu_tab1", "indicateurUn.noteFinale")

    def importIndicateurDeux(self):
        # Indicateur 2 relatif à l'écart de taux d'augmentations individuelles (hors promotion) entre
        # les femmes et les hommes pour les entreprises ou UES de plus de 250 salariés
        # Calculabilité
        self.importBooleanField("calculabilite_indic_tab2_sup250", "indicateurDeux.nonCalculable", negate=True)
        self.importField("motif_non_calc_tab2_sup250", "indicateurDeux.motifNonCalculable")
        self.importField("precision_am_tab2_sup250", "indicateurDeux.motifNonCalculablePrecision")
        # Taux d'augmentation individuelle par CSP
        self.importFloatField("Ou_tab2_sup250", "indicateurDeux.tauxAugmentation[0].ecartTauxAugmentation")
        self.importFloatField("Em_tab2_sup250", "indicateurDeux.tauxAugmentation[1].ecartTauxAugmentation")
        self.importFloatField("TAM_tab2_sup250", "indicateurDeux.tauxAugmentation[2].ecartTauxAugmentation")
        self.importFloatField("IC_tab2_sup250", "indicateurDeux.tauxAugmentation[3].ecartTauxAugmentation")
        # Résultats
        self.importFloatField("resultat_tab2_sup250", "indicateurDeux.resultatFinal")
        self.importField("population_favorable_tab2_sup250", "indicateurDeux.sexeSurRepresente")
        self.importIntField("nb_pt_obtenu_tab2_sup250", "indicateurDeux.noteFinale")
        # Prise de mesures correctives
        self.importBooleanField("prise_compte_mc_tab2_sup250", "indicateurDeux.mesuresCorrection")

    def importIndicateurTrois(self):
        # Indicateur 3 relatif à l'écart de taux de promotions entre les femmes et les hommes pour
        # les entreprises ou UES de plus de 250 salariés
        # Calculabilité
        self.importBooleanField("calculabilite_indic_tab3_sup250", "indicateurTrois.nonCalculable", negate=True)
        self.importField("motif_non_calc_tab3_sup250", "indicateurTrois.motifNonCalculable")
        self.importField("precision_am_tab3_sup250", "indicateurTrois.motifNonCalculablePrecision")
        # Ecarts de taux de promotions par CSP
        self.importField("Ou_tab3_sup250", "indicateurTrois.tauxPromotion[0].ecartTauxPromotion")
        self.importField("Em_tab3_sup250", "indicateurTrois.tauxPromotion[1].ecartTauxPromotion")
        self.importField("TAM_tab3_sup250", "indicateurTrois.tauxPromotion[2].ecartTauxPromotion")
        self.importField("IC_tab3_sup250", "indicateurTrois.tauxPromotion[3].ecartTauxPromotion")
        # Résultats
        self.importFloatField("resultat_tab3_sup250", "indicateurTrois.resultatFinal")
        self.importField("population_favorable_tab3_sup250", "indicateurTrois.sexeSurRepresente")
        self.importIntField("nb_pt_obtenu_tab3_sup250", "indicateurTrois.noteFinale")
        # Prise de mesures correctives
        self.importBooleanField("prise_compte_mc_tab3_sup250", "indicateurTrois.mesuresCorrection")

    def importNombreDePointsObtenus(self):
        # Nombre de points obtenus  à chaque indicateur attribué automatiquement
        self.importIntField("Indicateur 1", "declaration.indicateurUn")
        self.importIntField("Indicateur 2", "declaration.indicateurDeux")
        self.importIntField("Indicateur 2 PourCent", "declaration.indicateurDeuxTroisEcart")
        self.importIntField("Indicateur 2 ParSal", "declaration.indicateurDeuxTroisNombreSalaries")
        self.importIntField("Indicateur 3", "declaration.indicateurTrois")
        self.importIntField("Indicateur 4", "declaration.indicateurQuatre")
        self.importIntField("Indicateur 5", "declaration.indicateurCinq")

    def importNiveauDeResultatGlobal(self):
        self.importIntField("Nombre total de points obtenus", "declaration.noteFinale")
        self.importIntField("Nombre total de points pouvant être obtenus", "declaration.nombrePointsMax")
        self.importIntField("Résultat final sur 100 points", "declaration.noteFinaleSur100")
        self.importField("mesures_correction", "declaration.mesuresCorrection")


def processRow(row):
    importer = RowImporter(row)
    importer.importInformationsDeclarant()
    importer.importPeriodeDeReference()
    importer.importEntreprise()
    importer.importUES()
    importer.importNiveauResultat()
    importer.importIndicateurUn()
    importer.importIndicateurDeux()
    importer.importIndicateurTrois()
    # ...
    importer.importNombreDePointsObtenus()
    importer.importNiveauDeResultatGlobal()
    return importer.toKintoRecord()


def checkLocale():
    try:
        setlocale(LC_NUMERIC, "fr_FR.UTF-8")
    except Exception:
        print("Impossible d'utiliser la locale fr_FR.UTF-8 nécessaire à la conversion de décimaux en notation française ('19.2' et non '19,2')")
        exit(1)


def parse(args):
    checkLocale()
    result = []
    with open(args.csv_path) as csv_file:
        reader = csv.DictReader(csv_file)
        count_processed = 0
        count_imported = 0
        for index, row in enumerate(reader):
            if args.max and count_processed >= args.max:
                break
            if args.siren and row["SIREN_ets"] != args.siren and row["SIREN_UES"] != args.siren:
                continue
            try:
                result.append(processRow(row))
                count_imported = count_imported + 1
            except KeyError as err:
                printer.error("Error importing line {0}: Missing key {1}".format(index, err))
            except ValueError as err:
                printer.error("Error importing line {0}: {1}".format(index, err))
            except RuntimeError as err:
                printer.error("Error importing line {0}: {1}".format(index, err))
            count_processed = count_processed + 1
    if args.show_json:
        printer.std(json.dumps(result, indent=args.indent))
    if args.siren and count_processed == 0:
        printer.error("Aucune entrée trouvée pour le Siren " + args.siren)
    else:
        if count_processed == count_imported:
            printer.info("Aucune erreur rencontrée.")
        printer.success("{0}/{1} ligne(s) importée(s).".format(count_imported, count_processed))


class printer:
    HEADER = "\033[95m"
    INFO = "\033[94m"
    SUCCESS = "\033[92m"
    WARNING = "\033[93m"
    ERROR = "\033[91m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"
    UNDERLINE = "\033[4m"

    @staticmethod
    def std(str):
        print(str)

    @staticmethod
    def error(str):
        print(f"{printer.ERROR}✖  {str}{printer.ENDC}")

    @staticmethod
    def info(str):
        print(f"{printer.INFO}🛈  {str}{printer.ENDC}")

    @staticmethod
    def success(str):
        print(f"{printer.SUCCESS}✓  {str}{printer.ENDC}")

    @staticmethod
    def warn(str):
        print(f"{printer.WARNING}⚠️  {str}{printer.ENDC}")


parser = argparse.ArgumentParser(description="Import des données Solen.")
parser.add_argument("csv_path", type=str, help="chemin vers l'export CSV Solen")
parser.add_argument("--siren", type=str, help="importer le SIREN spécifié uniquement")
parser.add_argument("--indent", type=int, help="niveau d'indentation JSON", default=None)
parser.add_argument("--max", type=int, help="nombre maximum de lignes à importer", default=None)
parser.add_argument("--show-json", help="afficher la sortie JSON", action="store_true", default=False)

parse(parser.parse_args())