void id80(int array[256], unsigned int len){
  unsigned int i = 0;
  while (i < len) {
    i = (i & (~7));
    array[i] = i;
    i++;
  }
}