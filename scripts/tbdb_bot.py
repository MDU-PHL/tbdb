import sys
import argparse
import csv
import os
from github import Github
import subprocess
import re
import statsmodels.api as sm
from collections import defaultdict
from tqdm import tqdm
import json
import numpy as np

def github_pr_comment(added_results, removed_results):
	#["drug","gene","mutation","variant_type","table","OR","OR_pval","OR_pval","RR","RR_pval","RR_pval","confidence"]
	body = "## Mutation confidence results\n"
	if len(added_results)>0:
		body+= "### Mutations added\n| Drug | Gene | Mutation | OR | OR-pval | LR | LR-pval | confidence |\n|-|-|-|-|-|-|-|-|\n"
		for r in added_results:
			body+=("|%(drug)s|%(gene)s|%(mutation)s|%(OR)s|%(OR_pval)s|%(RR)s|%(RR_pval)s|%(confidence)s|\n" % r)
	if len(removed_results)>0:
		body+= "### Mutations removed\n| Drug | Gene | Mutation | OR | OR-pval | LR | LR-pval | confidence |\n|-|-|-|-|-|-|-|-|\n"
		for r in removed_results:
			body+=("|%(drug)s|%(gene)s|%(mutation)s|%(OR)s|%(OR_pval)s|%(RR)s|%(RR_pval)s|%(confidence)s|\n" % r)


	g = Github(os.environ["GH_AUTH_TOKEN"])
	repo = g.get_repo("jodyphelan/tbdb")
	print(list(repo.get_pulls()))
	pr = repo.get_pull(int(os.environ["CIRCLE_PULL_REQUEST"].split("/")[-1]))
	commit = repo.get_commit(sha=os.environ["CIRCLE_SHA1"])
	commit.create_comment(body)

def download_data():
	with open("/dev/null","w") as O:
		subprocess.call("wget http://pathogenseq.lshtm.ac.uk/downloads/tbprofiler_results.tgz", shell=True, stderr=O, stdout=O)
		subprocess.call("tar -xvf tbprofiler_results.tgz", shell=True, stderr=O, stdout=O)

def get_codon_number(x):
	re_obj = re.search("p.[A-Za-z]+([0-9]+)[A-Za-z\*]+",x)
	return re_obj.group(1)

def main_identify_new_mutations(args):

	download_data()
	gene2locustag = {}
	drug2genes = defaultdict(set)
	for l in open("tb.bed"):
		row = l.rstrip().split()
		gene2locustag[row[4]] = row[3]
		for d in row[5].split(","):
			drug2genes[d].add(row[3])

	mutations1 = set()
	mutations2 = set()
	for row in csv.DictReader(open(args.csv1)):
		mutations1.add((row["Drug"],gene2locustag[row["Gene"]],row["Mutation"]))
	for row in csv.DictReader(open(args.csv2)):
		mutations2.add((row["Drug"],gene2locustag[row["Gene"]],row["Mutation"]))

	diff_added = list(mutations2 - mutations1)
	diff_removed = list(mutations1 - mutations2)
	if len(diff_added)==0 and len(diff_removed)==0:
		sys.stdout.write("No mutations added or removed\n")
		quit()


	multi_change_codons = defaultdict(list)
	for drug,gene,mutation in diff_added:
		if "any_missense_codon_" in mutation:
			codon_num = mutation.replace("any_missense_codon_","")
			multi_change_codons[(gene,codon_num)].append(drug.lower())
	for drug,gene,mutation in diff_added:
		if "any_missense_codon_" in mutation:
			codon_num = mutation.replace("any_missense_codon_","")
			multi_change_codons[(gene,codon_num)].append(drug.lower())

	meta = {}
	for row in csv.DictReader(open("tb.dst.csv")):
		meta[row["id"]] = row

	samples = [x.replace(".results.json","") for x in os.listdir("%s/" % args.dir) if x[-13:]==".results.json"]

	variants = defaultdict(lambda:defaultdict(list))
	mutation_types = defaultdict(dict)
	sys.stderr.write("Loading tb-profiler results\n")
	for s in tqdm(samples):
		tmp = json.load(open("%s/%s.results.json" % (args.dir,s)))
		for var in tmp["dr_variants"]:
			variants[var["locus_tag"]][var["change"]].append(s)
			if "large_deletion" in var["type"]:
				variants[var["locus_tag"]]["large_deletion"].append(s)
			elif  "frameshift" in var["type"]:
				variants[var["locus_tag"]]["frameshift"].append(s)
			if var["type"]=="missense":
				codon_num = get_codon_number(var["change"])
				if (var["locus_tag"],codon_num) in multi_change_codons and var["drug"] in multi_change_codons[(var["locus_tag"],codon_num)]:
					variants[var["locus_tag"]]["any_missense_codon_"+codon_num].append(s)
			mutation_types[(var["locus_tag"],var["change"])] = var["type"]
		for var in tmp["other_variants"]:
			variants[var["locus_tag"]][var["change"]].append(s)
			if "large_deletion" in var["type"]:
				variants[var["locus_tag"]]["large_deletion"].append(s)
			elif  "frameshift" in var["type"]:
				variants[var["locus_tag"]]["frameshift"].append(s)
			if var["type"]=="missense":
				codon_num = get_codon_number(var["change"])
				if (var["locus_tag"],codon_num) in multi_change_codons and var["drug"] in multi_change_codons[(var["locus_tag"],codon_num)]:
					variants[var["locus_tag"]]["any_missense_codon_"+codon_num].append(s)
			mutation_types[(var["locus_tag"],var["change"])] = var["type"]

	print("Collected %s unique variants in %s genes" % (sum([len(variants[x]) for x in variants]),len(variants)))
	added_results = []
	for drug,gene,mutation in diff_added:
		if drug not in meta[samples[0]]: quit("%s not in meta" % drug)
		if gene not in variants: quit("%s not in genotype files" % gene)
		sys.stderr.write("Calculating metrics for %s with %s\n" % (gene,drug))

		result = {"gene":gene,"drug":drug,"mutation":mutation}
		t = [
				[0.5,0.5],
				[0.5,0.5]
			 ]
		for s in samples:
			if s not in meta: continue
			if meta[s][drug]=="1" and s in variants[gene][mutation]: 		t[0][0]+=1
			if meta[s][drug]=="0" and s in variants[gene][mutation]: 		t[0][1]+=1
			if meta[s][drug]=="1" and s not in variants[gene][mutation]: 	t[1][0]+=1
			if meta[s][drug]=="0" and s not in variants[gene][mutation]: 	t[1][1]+=1
		t2 = sm.stats.Table2x2(np.asarray(t))
		result["OR"] = t2.oddsratio
		result["OR_pval"] = t2.oddsratio_pvalue()
		result["RR"] = t2.riskratio
		result["RR_pval"] = t2.riskratio_pvalue()
		result["table"] = t
		result["variant_type"] = mutation_types[(gene,mutation)]
		added_results.append(result)

	removed_results = []
	for drug,gene,mutation in diff_removed:
		if drug not in meta[samples[0]]: quit("%s not in meta" % drug)
		if gene not in variants: quit("%s not in genotype files" % gene)
		sys.stderr.write("Calculating metrics for %s with %s\n" % (gene,drug))

		result = {"gene":gene,"drug":drug,"mutation":mutation}
		t = [
				[0.5,0.5],
				[0.5,0.5]
			 ]
		for s in samples:
			if s not in meta: continue
			if meta[s][drug]=="1" and s in variants[gene][mutation]: 		t[0][0]+=1
			if meta[s][drug]=="0" and s in variants[gene][mutation]: 		t[0][1]+=1
			if meta[s][drug]=="1" and s not in variants[gene][mutation]: 	t[1][0]+=1
			if meta[s][drug]=="0" and s not in variants[gene][mutation]: 	t[1][1]+=1
		t2 = sm.stats.Table2x2(np.asarray(t))
		result["OR"] = t2.oddsratio
		result["OR_pval"] = t2.oddsratio_pvalue()
		result["RR"] = t2.riskratio
		result["RR_pval"] = t2.riskratio_pvalue()
		result["table"] = t
		result["variant_type"] = mutation_types[(gene,mutation)]
		removed_results.append(result)

	for i in tqdm(range(len(added_results))):
		if added_results[i]["OR"]>10 and added_results[i]["OR_pval"]<args.pval_cutoff and added_results[i]["RR"]>1 and added_results[i]["RR_pval"]<args.pval_cutoff:
			added_results[i]["confidence"] = "high"
		elif 5<added_results[i]["OR"]<=10 and added_results[i]["OR_pval"]<args.pval_cutoff and added_results[i]["RR"]>1 and added_results[i]["RR_pval"]<args.pval_cutoff:
			added_results[i]["confidence"] = "moderate"
		elif 1<added_results[i]["OR"]<=5 and added_results[i]["OR_pval"]<args.pval_cutoff and added_results[i]["RR"]>1 and added_results[i]["RR_pval"]<args.pval_cutoff:
			added_results[i]["confidence"] = "low"
		elif (added_results[i]["OR"]<=1 and added_results[i]["OR_pval"]<args.pval_cutoff) or (added_results[i]["RR"]<=1 and added_results[i]["RR_pval"]<args.pval_cutoff):
			added_results[i]["confidence"] = "no_association"
		else:
			added_results[i]["confidence"] = "indeterminate"

	for i in tqdm(range(len(removed_results))):
		if removed_results[i]["OR"]>10 and removed_results[i]["OR_pval"]<args.pval_cutoff and removed_results[i]["RR"]>1 and removed_results[i]["RR_pval"]<args.pval_cutoff:
			removed_results[i]["confidence"] = "high"
		elif 5<removed_results[i]["OR"]<=10 and removed_results[i]["OR_pval"]<args.pval_cutoff and removed_results[i]["RR"]>1 and removed_results[i]["RR_pval"]<args.pval_cutoff:
			removed_results[i]["confidence"] = "moderate"
		elif 1<removed_results[i]["OR"]<=5 and removed_results[i]["OR_pval"]<args.pval_cutoff and removed_results[i]["RR"]>1 and removed_results[i]["RR_pval"]<args.pval_cutoff:
			removed_results[i]["confidence"] = "low"
		elif (removed_results[i]["OR"]<=1 and removed_results[i]["OR_pval"]<args.pval_cutoff) or (removed_results[i]["RR"]<=1 and removed_results[i]["RR_pval"]<args.pval_cutoff):
			removed_results[i]["confidence"] = "no_association"
		else:
			removed_results[i]["confidence"] = "indeterminate"


	if args.github:
		github_pr_comment(added_results, removed_results)

parser = argparse.ArgumentParser(description='TBDB bot',formatter_class=argparse.ArgumentDefaultsHelpFormatter)
subparsers = parser.add_subparsers(help="Task to perform")

parser_sub = subparsers.add_parser('compare', help='Run whole profiling pipeline', formatter_class=argparse.ArgumentDefaultsHelpFormatter)
parser_sub.add_argument('--csv1',help='Master csv',required=True)
parser_sub.add_argument('--csv2',help='Pull request csv',required=True)
parser_sub.add_argument('--out',type=str,default="confidence.csv", help="Output file")
parser_sub.add_argument('--dir',default="tbprofiler_results/",type=str,help='Firectory to look for tbprofiler results files')
parser_sub.add_argument('--pval-cutoff',default=0.05,type=float,help='Pvalue cutoff to use for the corrected OR and RR p-vaule significance')
parser_sub.add_argument('--github',action="store_true",help='Post results to github')
parser_sub.set_defaults(func=main_identify_new_mutations)

args = parser.parse_args()
if vars(args)=={}:
	parser.print_help(sys.stderr)
else:
	args.func(args)
